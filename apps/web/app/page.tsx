'use client'

import { useState, useMemo, useEffect, useRef } from 'react'
import useSWR from 'swr'
import Link from 'next/link'
import { useMarket } from '@/lib/market-context'
import { listCDSignals } from '@/lib/cd_signals_api'
import { fetchSymbolProfile } from '@/lib/symbol_api'
import { isMarketOpenNow, type Market } from '@/lib/markets'
import { useBarsHistory, mergeBarsAsc } from '@/lib/use_bars_history'
import { useKlineStream } from '@/lib/use_kline_stream'
import { KLineChart } from '@/components/KLineChart'
import type { BarDTO, Interval } from '@/lib/types'

// ── helpers ──
function pctChange(bars: BarDTO[]) {
  if (bars.length < 2) return null
  const a = bars[bars.length - 1].close, b = bars[bars.length - 2].close
  return { close: a, pct: b ? ((a - b) / b) * 100 : 0, high: bars[bars.length - 1].high, low: bars[bars.length - 1].low, vol: bars[bars.length - 1].volume ?? 0, open: bars[bars.length - 1].open }
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

const DEFAULT_WATCHLIST: Record<string, string[]> = {
  ashare: ['600519.SH', '300750.SZ', '002594.SZ', '603259.SH', '688981.SH', '002371.SZ', '300059.SZ'],
  us: ['AAPL', 'NVDA', 'MSFT', 'TSLA', 'AMZN', 'META', 'AMD'],
  crypto: ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'XRP-USDT', 'TRX-USDT'],
  hk: ['00700.HK', '09988.HK', '03690.HK'],
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
  const { market } = useMarket()
  const indices = INDEX_CONFIG[market] || INDEX_CONFIG.ashare
  const watchlist = DEFAULT_WATCHLIST[market] || DEFAULT_WATCHLIST.ashare

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
  const { data: wlRest } = useSWR(`wl:${market}`,
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

  // 价格变动 → 对应行闪动 (涨绿跌红)
  useEffect(() => {
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
  const displayBars = useMemo(() => mergeBarsAsc(hist.bars, streamBars), [hist.bars, streamBars])

  // 信号
  const { data: signalsResp } = useSWR(`signals:${market}`,
    () => listCDSignals({ market, limit: 20 }), { refreshInterval: 60_000 })
  const signals = (signalsResp?.signals ?? []).slice(0, 4)

  return (
    <div style={{ padding: 20 }}>
      {/* Index Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12, marginBottom: 20 }}>
        {indices.map(idx => {
          const bars = (idxBars ?? {})[idx.symbol] ?? []
          const pc = pctChange(bars); const up = (pc?.pct ?? 0) >= 0
          return (
            <Link key={idx.symbol} href={`/symbol/${encodeURIComponent(idx.symbol)}`} style={{ textDecoration: 'none', color: 'inherit' }}>
              <div className={`idx-card ${idx.symbol === 'BTC-USDT' ? 'crypto-idx' : ''}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontSize: 13, color: 'var(--text2)', fontWeight: 500 }}>{idx.name}</span>
                  <span style={{ fontSize: 10, color: 'var(--text3)', fontFamily: 'monospace' }}>{idx.symbol}</span>
                </div>
                <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace' }}>
                  {pc != null ? pc.close.toFixed(2) : '—'}
                </div>
                <div style={{ fontSize: 12, fontFamily: 'monospace' }} className={up ? 'text-up' : 'text-down'}>
                  {pc != null ? `${up ? '+' : ''}${pc.pct.toFixed(2)}%` : '—'}
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
            <Link href="/watchlist" style={{ fontSize: 12, color: 'var(--accent)' }}>管理 →</Link>
          </div>
          <table className="data-table">
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
                        ...(pc == null ? {} : up ? { background: 'rgba(52,211,153,0.12)', color: 'var(--green)' }
                          : pc.pct < 0 ? { background: 'rgba(248,113,113,0.12)', color: 'var(--red)' }
                          : { color: 'var(--text2)' })
                      }}>{pc != null ? `${pc.pct > 0 ? '+' : ''}${pc.pct.toFixed(2)}%` : '—'}</span>
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
              <span style={{ fontSize: 16, fontWeight: 700, fontFamily: 'monospace' }}>{selected}</span>
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
          <div style={{ margin: '0 4px 8px' }}>
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

      {/* Bottom: Signals + Info */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div className="panel">
          <div className="panel-header">
            🎯 最近信号 · {market === 'ashare' ? 'A股' : market === 'us' ? '美股' : market === 'crypto' ? 'Crypto' : market}
            <Link href={`/notifications?market=${market}`} style={{ fontSize: 12, color: 'var(--accent)' }}>全部 →</Link>
          </div>
          <div>
            {signals.length === 0 && <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>暂无信号</div>}
            {signals.map((s: any, i: number) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 18px', borderBottom: '1px solid var(--border)' }}>
                <div className={`sig-badge ${s.signal_type === 'buy' ? 'buy' : 'sell'}`}>{s.signal_type === 'buy' ? '📈' : '📉'}</div>
                <div style={{ flex: 1 }}>
                  <Link href={`/symbol/${encodeURIComponent(s.symbol)}`} style={{ fontWeight: 600, color: 'inherit', textDecoration: 'none' }}>{s.symbol}</Link>
                  <div style={{ fontSize: 12, color: 'var(--text2)' }}>{s.signal_type === 'buy' ? '底背离买入' : '顶背离卖出'} · {s.interval}</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{s.bar_ts}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="panel-header"><MarketStatus market={market} /> · {market === 'crypto' ? 'Binance WS 实时' : market === 'us' ? 'Alpaca WS 实时' : 'bar_poller 10s'}</div>
          <div style={{ padding: '14px 18px' }}>
            <p style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.8 }}>
              点击<b>自选列表</b>切换预览标的 · <b>鼠标滚轮缩放</b> · <b>拖拽回看历史</b> · <b>详情 →</b> 全屏 K 线
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
