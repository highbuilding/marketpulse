'use client'

import { useState, useMemo, useEffect, useRef } from 'react'
import useSWR from 'swr'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useMarket } from '@/lib/market-context'
import { fetchSymbolProfile } from '@/lib/symbol_api'
import { fetchHealth } from '@/lib/api'
import { listWatchlists, listWatchlistSymbols } from '@/lib/watchlist_api'
import { useActiveWatchlistId } from '@/lib/use_active_watchlist'
import { isMarketOpenNow, inferMarket, type Market } from '@/lib/markets'
import { useBarsHistory, mergeTail } from '@/lib/use_bars_history'
import { useKlineStream } from '@/lib/use_kline_stream'
import { KLineChart } from '@/components/KLineChart'
import { PositionsPanel } from '@/components/PositionsPanel'
import type { BarDTO, Interval } from '@/lib/types'

// ── helpers ──
function pctChange(bars: BarDTO[]) {
  if (bars.length === 0) return null
  const last = bars[bars.length - 1]
  // 单根: 先显示最新价, 涨跌暂为 null(首屏 SSE 单根先到、REST 2 根未到时不再整列显 —)
  if (bars.length < 2) {
    return { close: last.close, pct: null as number | null, high: last.high, low: last.low, vol: last.volume ?? 0, open: last.open }
  }
  const a = last.close, b = bars[bars.length - 2].close
  return { close: a, pct: b ? ((a - b) / b) * 100 : 0, high: last.high, low: last.low, vol: last.volume ?? 0, open: last.open }
}

function MarketStatus({ market }: { market: string }) {
  const open = isMarketOpenNow(market as Market)
  const label = market === 'crypto' ? '24×7' : market === 'us' ? (open ? '交易中' : '休市') : market === 'ashare' ? (open ? '交易中' : '休市') : '—'
  const color = open ? 'var(--green)' : 'var(--text3)'
  return <span style={{ fontSize: 11, color, display: 'flex', alignItems: 'center', gap: 4 }}>
    <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, display: 'inline-block' }} />{label}
  </span>
}

// ── config ──
const INDEX_CONFIG: Record<string, { symbol: string; name: string }[]> = {
  ashare: [
    { symbol: '000001.SH', name: '上证指数' }, { symbol: '399001.SZ', name: '深证成指' },
    { symbol: '000300.SH', name: '沪深 300' }, { symbol: '399006.SZ', name: '创业板指' },
    { symbol: '000688.SH', name: '科创 50' }, { symbol: '000905.SH', name: '中证 500' },
    { symbol: '000016.SH', name: '上证 50' }, { symbol: '000852.SH', name: '中证 1000' },
  ],
  us: [
    { symbol: 'SPY', name: 'S&P 500' }, { symbol: 'QQQ', name: 'NASDAQ 100' },
    { symbol: 'DIA', name: 'DJIA' }, { symbol: 'IWM', name: 'Russell 2000' },
  ],
  crypto: [
    { symbol: 'BTC-USDT', name: 'Bitcoin' }, { symbol: 'ETH-USDT', name: 'Ethereum' },
    { symbol: 'SOL-USDT', name: 'Solana' }, { symbol: 'XRP-USDT', name: 'XRP' },
  ],
  hk: [{ symbol: 'HSI.HK', name: '恒生指数' }, { symbol: 'HSTECH.HK', name: '恒生科技' }],
}

// batch SSE: 1 连接推送 N 标的实时价
function useBatchStream(symbols: string[], interval: string) {
  const [prices, setPrices] = useState<Record<string, BarDTO[]>>({})
  const key = useMemo(() => [...symbols].sort().join(','), [symbols])
  useEffect(() => {
    if (symbols.length === 0) return
    const apiBase = typeof window !== 'undefined' && window.location.port === '3000' ? 'http://127.0.0.1:8787' : ''
    const url = `${apiBase}/api/sse/bars/batch?symbols=${encodeURIComponent(symbols.join(','))}&interval=${interval}`
    const es = new EventSource(url)
    const toDTO = (e: any): BarDTO => ({ ts: e.ts, open: e.open, high: e.high, low: e.low, close: e.close, volume: e.volume })
    es.addEventListener('init', (msg: any) => {
      const d = JSON.parse(msg.data); const sym = d.symbol
      if (!sym) return
      const dtos = (d.bars || []).map(toDTO)
      setPrices(prev => { const next = { ...prev }; const ex = next[sym] || []; const m = new Map<string, BarDTO>(); for (const b of ex) m.set(b.ts, b); for (const b of dtos) m.set(b.ts, b); next[sym] = Array.from(m.values()).sort((a, b) => a.ts.localeCompare(b.ts)); return next })
    })
    const onUpdate = (msg: any) => {
      const ev = JSON.parse(msg.data); const sym = ev.symbol
      if (!sym) return
      const dto = toDTO(ev)
      setPrices(prev => { const next = { ...prev }; const pb = next[sym] || []; const last = pb[pb.length - 1]; if (last && last.ts === dto.ts) next[sym] = [...pb.slice(0, -1), dto]; else if (!last || dto.ts > last.ts) next[sym] = [...pb, dto]; return next })
    }
    es.addEventListener('bar', onUpdate); es.addEventListener('tick', onUpdate)
    es.onerror = () => {}
    return () => es.close()
  }, [key, interval])
  return prices
}

function SymbolName({ symbol }: { symbol: string }) {
  const { data: profile } = useSWR(`p:${symbol}`, () => fetchSymbolProfile(symbol), { revalidateOnFocus: false })
  return <span style={{ fontWeight: 500 }}>{profile?.name ?? symbol}</span>
}

// ── Page ──
export default function HomePage() {
  const { market, setMarket } = useMarket()
  const router = useRouter()
  const indices = INDEX_CONFIG[market] || INDEX_CONFIG.ashare

  // 读真实自选(与 /watchlist 同源 + 共享同一清单选择):
  // 用共享的 activeId(自选页切清单 → 这里跟随);未设时取第一个清单。
  const [activeId] = useActiveWatchlistId()
  const { data: realWatchlist } = useSWR(['overview:watchlist', activeId], async () => {
    const { watchlists } = await listWatchlists()
    const chosen = (activeId != null && watchlists.find((w) => w.id === activeId))
      || watchlists.find((w) => !w.is_archived) || watchlists[0]
    if (!chosen) return null
    const { symbols } = await listWatchlistSymbols(chosen.id)
    return symbols
  }, { revalidateOnFocus: false })

  // 与自选页完全同源: 真实自选按当前市场过滤; 为空就显示空(不再用假数据兜底)。
  const watchlist = useMemo(() => {
    if (!realWatchlist) return []
    return realWatchlist.filter((s) => inferMarket(s) === market)
  }, [realWatchlist, market])

  // 行级 refs + 历史价: 价格变动 → DOM 闪动 (涨绿跌红)
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({})
  const prevPrices = useRef<Record<string, number>>({})

  // 指数 + 自选: REST 日线 60s
  const allSyms = useMemo(() => [...indices.map(i => i.symbol), ...watchlist], [indices, watchlist])
  const { data: idxBars } = useSWR(`idx:${market}`,
    () => Promise.all(indices.map(s => fetch(`/api/symbols/${encodeURIComponent(s.symbol)}/bars/history?interval=1d&limit=2`)
      .then(r => r.json()).then(d => (d.bars ?? []) as BarDTO[]).catch(() => [] as BarDTO[])))
      .then(r => { const m: Record<string, BarDTO[]> = {}; indices.forEach((x, i) => { m[x.symbol] = r[i] ?? [] }); return m }),
    { refreshInterval: 60_000, revalidateOnFocus: false })

  // 自选价格: REST 初始 + batch SSE 实时推送
  // key 必须含 watchlist 内容: 否则清单从空(realWatchlist 未到)变满时 key 不变,
  // SWR 认为已有数据不重新取 → 刷新后价格空, 要重新挂载(进详情再返回)才出现。
  // 清单空时 key=null, SWR 跳过请求(不发无谓的 0 标的请求)。
  const wlKey = watchlist.length ? `wl:${market}:${watchlist.join(',')}` : null
  const { data: wlRest } = useSWR(wlKey,
    () => Promise.all(watchlist.map(s => fetch(`/api/symbols/${encodeURIComponent(s)}/bars/history?interval=5m&limit=2`)
      .then(r => r.json()).then(d => (d.bars ?? []) as BarDTO[]).catch(() => [] as BarDTO[])))
      .then(r => { const m: Record<string, BarDTO[]> = {}; watchlist.forEach((s, i) => { m[s] = r[i] ?? [] }); return m }),
    { refreshInterval: 60_000, revalidateOnFocus: false })
  const streamPrices = useBatchStream(watchlist, '5m')

  // 合并: SSE > REST
  const wlBars = useMemo(() => {
    const merged: Record<string, BarDTO[]> = {}
    for (const sym of watchlist) {
      const rest = (wlRest ?? {})[sym] ?? []
      const sse = streamPrices[sym] ?? []
      if (sse.length > 0) {
        const map = new Map<string, BarDTO>()
        for (const b of rest) map.set(b.ts, b)
        for (const b of sse) map.set(b.ts, b)
        merged[sym] = Array.from(map.values()).sort((a, b) => a.ts.localeCompare(b.ts))
      } else {
        merged[sym] = rest
      }
    }
    return merged
  }, [wlRest, streamPrices, watchlist])

  // 价格变动 → 对应行闪动 (涨绿跌红)。数据源离线时不闪(避免假装实时)。
  useEffect(() => {
    if (offline) return
    for (const sym of watchlist) {
      const bars = (wlBars ?? {})[sym] ?? []
      if (bars.length === 0) continue
      const price = bars[bars.length - 1].close
      const prev = prevPrices.current[sym]
      if (prev !== undefined && price !== prev) {
        const row = rowRefs.current[sym]
        if (row) {
          const cls = price > prev ? 'flash-row-up' : 'flash-row-down'
          row.classList.remove('flash-row-up', 'flash-row-down')
          // eslint-disable-next-line @typescript-eslint/no-unused-expressions
          void row.offsetWidth  // 强制 reflow, 确保 CSS animation 重启
          row.classList.add(cls)
          setTimeout(() => {
            row.classList.remove('flash-row-up', 'flash-row-down')
          }, 950)
        }
      }
      prevPrices.current[sym] = price
    }
  }, [wlBars, watchlist])

  // 图表: useBarsHistory (分页) + useKlineStream (SSE) → KLineChart
  // 每个市场记住用户最后选的标的, 切换市场时恢复; 未操作过默认 watchlist[0]
  const marketSelectedRef = useRef<Record<string, string>>({})
  const [selected, setSelected] = useState(watchlist[0])
  useEffect(() => {
    const saved = marketSelectedRef.current[market]
    const next = (saved && watchlist.includes(saved)) ? saved : watchlist[0]
    setSelected(next)
  }, [market, watchlist])
  const [chartIv, setChartIv] = useState<Interval>('5m')
  const hist = useBarsHistory(selected, chartIv, market, { enabled: true, poll: false })
  const streamBars = useKlineStream(selected, chartIv, true)
  const displayBars = useMemo(() => mergeTail(hist.bars, streamBars), [hist.bars, streamBars])

  // 数据源健康: 离线时价格区标灰 + 关闪烁(crypto→binance, 其余同名)
  const { data: health } = useSWR('health', fetchHealth, { refreshInterval: 30_000 })
  const adapterKey = market === 'crypto' ? 'binance' : market
  const adapterState = health?.adapters?.[adapterKey as Market]?.state
  const offline = adapterState === 'down' || adapterState === 'degraded'

  return (
    <div style={{ padding: 20 }}>
      {/* Index Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12, marginBottom: 20 }}>
        {indices.map(idx => {
          const bars = (idxBars ?? {})[idx.symbol] ?? []
          const fb = pctChange(bars)
          // 指数与自选统一走 bar 数据源(1d bars/history): close=最新收线, pct=日涨跌。
          // 盘中 bars/history 末根即当日进行中根, 非盘中=今日收盘根, 天然满足实时/收盘快照。
          const close = fb?.close ?? null
          const pct = fb?.pct ?? null
          const up = (pct ?? 0) >= 0
          return (
            <Link key={idx.symbol} href={`/symbol/${encodeURIComponent(idx.symbol)}`} style={{ textDecoration: 'none', color: 'inherit' }}>
              <div className={`idx-card ${idx.symbol === 'BTC-USDT' ? 'crypto-idx' : ''}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontSize: 13, color: 'var(--text2)', fontWeight: 500 }}>{idx.name}</span>
                  <span style={{ fontSize: 10, color: 'var(--text3)', fontFamily: 'monospace' }}>{idx.symbol}</span>
                </div>
                <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace' }}>
                  {close != null ? close.toFixed(2) : '—'}
                </div>
                <div style={{ fontSize: 12, fontFamily: 'monospace' }} className={up ? 'text-up' : 'text-down'}>
                  {pct != null ? `${up ? '+' : ''}${pct.toFixed(2)}%` : '—'}
                </div>
              </div>
            </Link>
          )
        })}
      </div>

      {/* Watchlist + Chart */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: 16, marginBottom: 16 }}>
        <div className="panel">
          <div className="panel-header">
            ⭐ 自选 · <MarketStatus market={market} />
            {offline && <span style={{ fontSize: 11, color: 'var(--red)', marginLeft: 8 }}>● 数据源离线·价格可能过期</span>}
            <Link href="/watchlist" style={{ fontSize: 12, color: 'var(--accent)' }}>管理 →</Link>
          </div>
          <table className="data-table" style={{ opacity: offline ? 0.5 : 1, transition: 'opacity 0.3s' }}>
            <thead><tr><th>名称</th><th>最新价</th><th>涨跌</th></tr></thead>
            <tbody>
              {watchlist.map(sym => {
                const bars = (wlBars ?? {})[sym] ?? []
                const pc = pctChange(bars); const up = (pc?.pct ?? 0) >= 0
                return (
                  <tr key={sym} ref={(el) => { rowRefs.current[sym] = el }} onClick={() => { marketSelectedRef.current[market] = sym; setSelected(sym) }}
                    style={{ background: sym === selected ? 'var(--accent-bg)' : undefined, cursor: 'pointer', transition: 'background 0.3s' }}>
                    <td>
                      <span style={{ fontWeight: 500, display: 'block' }}><SymbolName symbol={sym} /></span>
                      <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>{sym}</span>
                    </td>
                    <td style={{ fontFamily: 'monospace' }}>{pc?.close.toFixed(2) ?? '—'}</td>
                    <td>
                      <span style={{ fontSize: 11, padding: '3px 6px', borderRadius: 4,
                        ...(pc == null || pc.pct == null ? {} : pc.pct > 0 ? { background: 'rgba(52,211,153,0.12)', color: 'var(--green)' }
                          : pc.pct < 0 ? { background: 'rgba(248,113,113,0.12)', color: 'var(--red)' }
                          : { color: 'var(--text2)' })
                      }}>{pc != null && pc.pct != null ? `${pc.pct > 0 ? '+' : ''}${pc.pct.toFixed(2)}%` : '—'}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* KLineChart (已有滑动翻页 + 缩放) */}
        <div className="panel">
          <div className="panel-header" style={{ borderBottom: 'none' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span style={{ fontSize: 16, fontWeight: 700 }}><SymbolName symbol={selected} /></span>
              <span style={{ fontSize: 12, color: 'var(--text3)', fontFamily: 'monospace' }}>{selected}</span>
              {offline && <span style={{ fontSize: 11, color: 'var(--red)' }}>● 数据源离线·价格可能过期</span>}
              <Link href={`/symbol/${encodeURIComponent(selected)}`}
                style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none', fontWeight: 600 }}>详情 →</Link>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 4, padding: '0 18px 6px' }}>
            {(['5m','15m','30m','60m','4h','1d','1wk','1mo'] as Interval[]).map(iv => (
              <button key={iv} onClick={() => setChartIv(iv)} className={`int-tab ${chartIv === iv ? 'active' : ''}`}>
                {iv === '60m' ? '1h' : iv}</button>
            ))}
          </div>
          <div style={{ margin: '0 4px 8px', opacity: offline ? 0.5 : 1, transition: 'opacity 0.3s' }}>
            {displayBars.length > 0 ? (
              <KLineChart bars={displayBars} interval={chartIv} market={market as Market} height={280}
                onLoadMore={hist.loadMore} hasMore={hist.hasMore} loadingMore={hist.loadingMore} />
            ) : (
              <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8 }}>
                {hist.loading ? '加载中...' : '无数据'}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Bottom: 持仓 (手动填入, 仅A股) */}
      <PositionsPanel />
    </div>
  )
}
