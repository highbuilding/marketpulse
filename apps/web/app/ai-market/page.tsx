'use client'

import useSWR from 'swr'

import { fetchAIMarketPacket } from '@/lib/api'
import { isMarketOpenNow } from '@/lib/markets'
import type {
  AIMarketEvent, AIMarketRank, AIMarketSector, AIMarketSymbol,
} from '@/lib/types'

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function pctClass(v: number | null | undefined): string {
  if (v == null) return 'text-neutral-500'
  if (v > 0) return 'text-red-400'
  if (v < 0) return 'text-green-400'
  return 'text-neutral-300'
}

function fmtAmount(v: number | null | undefined): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)} 万亿`
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(0)
}

function fmtVolume(v: number | null | undefined): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(0)
}

function SymbolMiniTable({ rows }: { rows: AIMarketSymbol[] }) {
  return (
    <div className="rounded border border-neutral-800 overflow-hidden">
      <div className="grid grid-cols-[1fr_80px_80px_90px] gap-2 px-3 py-2 text-xs text-neutral-500 bg-neutral-900/70">
        <span>标的</span>
        <span className="text-right">价格</span>
        <span className="text-right">涨跌幅</span>
        <span className="text-right">成交量</span>
      </div>
      {rows.length === 0 ? (
        <p className="px-3 py-4 text-sm text-neutral-500">暂无关注股数据</p>
      ) : rows.map((row) => (
        <a
          key={row.symbol}
          href={`/symbol/${encodeURIComponent(row.symbol)}`}
          className="grid grid-cols-[1fr_80px_80px_90px] gap-2 px-3 py-2 text-sm border-t border-neutral-800 hover:bg-neutral-900"
        >
          <span className="min-w-0">
            <span className="font-mono text-xs text-neutral-400">{row.symbol}</span>
            <span className="ml-2 text-neutral-200">{row.name ?? '—'}</span>
            {row.sectors.length > 0 && (
              <span className="ml-2 text-xs text-neutral-500">{row.sectors.slice(0, 2).join(' / ')}</span>
            )}
          </span>
          <span className="text-right tabular-nums">{row.price?.toFixed(2) ?? '—'}</span>
          <span className={`text-right tabular-nums ${pctClass(row.change_pct)}`}>{fmtPct(row.change_pct)}</span>
          <span className="text-right tabular-nums text-neutral-400">{fmtVolume(row.volume)}</span>
        </a>
      ))}
    </div>
  )
}

function RankTable({ rows }: { rows: AIMarketRank[] }) {
  return (
    <div className="rounded border border-neutral-800 overflow-hidden">
      <div className="grid grid-cols-[1fr_74px_90px] gap-2 px-3 py-2 text-xs text-neutral-500 bg-neutral-900/70">
        <span>标的</span>
        <span className="text-right">涨跌幅</span>
        <span className="text-right">成交额</span>
      </div>
      {rows.map((row) => (
        <a
          key={row.symbol}
          href={`/symbol/${encodeURIComponent(row.symbol)}`}
          className="grid grid-cols-[1fr_74px_90px] gap-2 px-3 py-2 text-sm border-t border-neutral-800 hover:bg-neutral-900"
        >
          <span className="truncate">
            <span className="font-mono text-xs text-neutral-500">{row.symbol}</span>
            <span className="ml-2">{row.name}</span>
          </span>
          <span className={`text-right tabular-nums ${pctClass(row.change_pct)}`}>{fmtPct(row.change_pct)}</span>
          <span className="text-right tabular-nums text-neutral-400">{fmtAmount(row.amount)}</span>
        </a>
      ))}
    </div>
  )
}

function SectorList({ rows }: { rows: AIMarketSector[] }) {
  return (
    <div className="rounded border border-neutral-800 overflow-hidden">
      <div className="grid grid-cols-[1fr_78px_1fr] gap-2 px-3 py-2 text-xs text-neutral-500 bg-neutral-900/70">
        <span>板块</span>
        <span className="text-right">涨跌幅</span>
        <span>领涨/核心线索</span>
      </div>
      {rows.map((row) => (
        <div key={row.name} className="grid grid-cols-[1fr_78px_1fr] gap-2 px-3 py-2 text-sm border-t border-neutral-800">
          <span className="truncate">{row.name}</span>
          <span className={`text-right tabular-nums ${pctClass(row.change_pct)}`}>{fmtPct(row.change_pct)}</span>
          <span className="truncate text-neutral-400">
            {row.leader_name} <span className={pctClass(row.leader_change_pct)}>{fmtPct(row.leader_change_pct)}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

function EventList({ rows }: { rows: AIMarketEvent[] }) {
  return (
    <div className="space-y-2">
      {rows.length === 0 ? (
        <p className="text-sm text-neutral-500">暂无显著触发事件</p>
      ) : rows.map((event, idx) => (
        <div key={`${event.category}-${idx}`} className="rounded border border-neutral-800 bg-neutral-950 px-3 py-2">
          <div className="flex items-center justify-between gap-3">
            <span className={event.level === 'warning' ? 'text-yellow-300' : 'text-blue-300'}>{event.title}</span>
            <span className="text-xs text-neutral-500">{event.category}</span>
          </div>
          <p className="mt-1 text-sm text-neutral-400">{event.detail}</p>
          {event.symbols.length > 0 && (
            <p className="mt-1 text-xs text-neutral-500 truncate">关联：{event.symbols.slice(0, 8).join('、')}</p>
          )}
        </div>
      ))}
    </div>
  )
}

export default function AIMarketPage() {
  const { data, error, isLoading } = useSWR('ai-market-packet', fetchAIMarketPacket, {
    refreshInterval: () => isMarketOpenNow('ashare') ? 60_000 : 0,
  })

  const breadth = data?.breadth
  const updatedAt = data
    ? new Date(data.generated_at).toLocaleTimeString('zh-CN', { hour12: false })
    : '—'
  const upRatio = breadth && breadth.total > 0 ? breadth.advancers / breadth.total : 0
  const downRatio = breadth && breadth.total > 0 ? breadth.decliners / breadth.total : 0

  return (
    <div style={{ padding: 20 }}>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>🤖 AI 盘面数据包</h1>
          <p style={{ color: 'var(--text2)', fontSize: 13 }}>程序负责聚合和触发，AI 负责基于事实总结。自动刷新 60 秒。</p>
        </div>
        <div style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>更新 {updatedAt}</div>
      </div>

      {error && <div className="rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">盘面数据包加载失败</div>}
      {isLoading && <div className="rounded border border-neutral-800 bg-neutral-950 p-4 text-sm text-neutral-500">载入中</div>}

      {data && (
        <>
          {data.degraded.length > 0 && (
            <section className="rounded border border-yellow-900/70 bg-yellow-950/20 p-3 text-sm text-yellow-200">
              数据降级：{data.degraded.join('；')}
            </section>
          )}

          <section className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="rounded border border-neutral-800 bg-neutral-950 p-4">
              <div className="text-xs text-neutral-500">全 A 样本</div>
              <div className="mt-2 text-2xl tabular-nums">{breadth?.total ?? 0}</div>
            </div>
            <div className="rounded border border-neutral-800 bg-neutral-950 p-4">
              <div className="text-xs text-neutral-500">上涨 / 下跌</div>
              <div className="mt-2 text-2xl tabular-nums">
                <span className="text-red-400">{breadth?.advancers ?? 0}</span>
                <span className="mx-2 text-neutral-600">/</span>
                <span className="text-green-400">{breadth?.decliners ?? 0}</span>
              </div>
              <div className="mt-2 h-1.5 rounded bg-neutral-800 overflow-hidden">
                <div className="h-full bg-red-500 inline-block" style={{ width: `${Math.round(upRatio * 100)}%` }} />
                <div className="h-full bg-green-500 inline-block" style={{ width: `${Math.round(downRatio * 100)}%` }} />
              </div>
            </div>
            <div className="rounded border border-neutral-800 bg-neutral-950 p-4">
              <div className="text-xs text-neutral-500">涨停 / 跌停</div>
              <div className="mt-2 text-2xl tabular-nums">
                <span className="text-red-400">{breadth?.up_limit ?? 0}</span>
                <span className="mx-2 text-neutral-600">/</span>
                <span className="text-green-400">{breadth?.down_limit ?? 0}</span>
              </div>
            </div>
            <div className="rounded border border-neutral-800 bg-neutral-950 p-4">
              <div className="text-xs text-neutral-500">全 A 成交额</div>
              <div className="mt-2 text-2xl tabular-nums">{fmtAmount(breadth?.total_amount)}</div>
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-2">
              <h2 className="text-sm text-neutral-400">核心指数</h2>
              <SymbolMiniTable rows={data.indices} />
            </div>
            <div className="space-y-2">
              <h2 className="text-sm text-neutral-400">触发事件</h2>
              <EventList rows={data.events} />
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-2">
              <h2 className="text-sm text-neutral-400">热门板块</h2>
              <SectorList rows={data.hot_sectors} />
            </div>
            <div className="space-y-2">
              <h2 className="text-sm text-neutral-400">弱势板块</h2>
              <SectorList rows={data.weak_sectors} />
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="space-y-2">
              <h2 className="text-sm text-neutral-400">全 A 强势</h2>
              <RankTable rows={data.top_gainers} />
            </div>
            <div className="space-y-2">
              <h2 className="text-sm text-neutral-400">全 A 弱势</h2>
              <RankTable rows={data.top_losers} />
            </div>
          </section>

          <section className="space-y-2">
            <h2 className="text-sm text-neutral-400">我的关注 / 持仓观察池</h2>
            <SymbolMiniTable rows={data.watchlist} />
          </section>

          <section className="space-y-2">
            <h2 className="text-sm text-neutral-400">给 AI 的结构化输入</h2>
            <pre className="max-h-[360px] overflow-auto rounded border border-neutral-800 bg-neutral-950 p-3 text-xs text-neutral-300">
              {JSON.stringify(data.ai_brief, null, 2)}
            </pre>
          </section>
        </>
      )}
    </div>
  )
}
