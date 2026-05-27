'use client'

import useSWR from 'swr'

import { fetchAIMarketPacket } from '@/lib/api'
import type { AIMarketEvent, AIMarketRank, AIMarketSymbol } from '@/lib/types'

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

function levelClass(level: string): string {
  if (level === 'warning') return 'border-yellow-900/70 bg-yellow-950/20 text-yellow-100'
  if (level === 'positive') return 'border-red-900/70 bg-red-950/20 text-red-100'
  return 'border-neutral-800 bg-neutral-950 text-neutral-200'
}

function EventRow({ event }: { event: AIMarketEvent }) {
  return (
    <div className={`rounded border p-3 ${levelClass(event.level)}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{event.title}</p>
          <p className="mt-1 text-xs text-neutral-400">{event.detail}</p>
        </div>
        <span className="shrink-0 rounded border border-current px-1.5 py-0.5 text-[10px] uppercase opacity-80">
          {event.category}
        </span>
      </div>
    </div>
  )
}

function RankList({ title, rows }: { title: string; rows: AIMarketRank[] }) {
  return (
    <div className="rounded border border-neutral-800 bg-neutral-950 p-3">
      <h3 className="mb-2 text-xs text-neutral-500">{title}</h3>
      <div className="space-y-1">
        {rows.slice(0, 6).map((row) => (
          <a
            key={row.symbol}
            href={`/symbol/${encodeURIComponent(row.symbol)}`}
            className="flex items-center justify-between gap-2 text-sm hover:text-neutral-100"
          >
            <span className="min-w-0 truncate">
              <span className="font-mono text-[11px] text-neutral-500">{row.symbol}</span>
              <span className="ml-1 text-neutral-300">{row.name}</span>
            </span>
            <span className={`shrink-0 tabular-nums ${pctClass(row.change_pct)}`}>{fmtPct(row.change_pct)}</span>
          </a>
        ))}
      </div>
    </div>
  )
}

function WatchRow({ item }: { item: AIMarketSymbol }) {
  return (
    <a
      href={`/symbol/${encodeURIComponent(item.symbol)}`}
      className="flex items-center justify-between gap-2 rounded border border-neutral-800 bg-neutral-950 px-3 py-2 text-sm hover:border-neutral-600"
    >
      <span className="min-w-0 truncate">
        <span className="font-mono text-[11px] text-neutral-500">{item.symbol}</span>
        <span className="ml-1 text-neutral-300">{item.name ?? '—'}</span>
      </span>
      <span className={`shrink-0 tabular-nums ${pctClass(item.change_pct)}`}>{fmtPct(item.change_pct)}</span>
    </a>
  )
}

export function IntradayRadarPanel() {
  const { data, error, isLoading } = useSWR('ai-market-packet', fetchAIMarketPacket, {
    refreshInterval: 60_000,
  })

  return (
    <section className="space-y-3">
      <header>
        <h2 className="text-sm text-neutral-400">盘中异动雷达</h2>
        <p className="mt-1 text-xs text-neutral-500">程序规则先识别宽度、风格、板块、涨跌停和自选股异动，后续再作为 AI 输入。</p>
      </header>

      {isLoading && <div className="rounded border border-neutral-800 bg-neutral-950 p-4 text-sm text-neutral-500">加载中</div>}
      {error && <div className="rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">异动数据加载失败</div>}

      {data && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.2fr_1fr]">
          <div className="space-y-2">
            {data.events.length === 0 ? (
              <div className="rounded border border-neutral-800 bg-neutral-950 p-4 text-sm text-neutral-500">暂无显著异动</div>
            ) : data.events.map((event, idx) => <EventRow key={`${event.category}:${idx}`} event={event} />)}
          </div>

          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-1">
              <RankList title="全 A 快速涨幅榜" rows={data.top_gainers} />
              <RankList title="全 A 快速跌幅榜" rows={data.top_losers} />
            </div>
            <div className="rounded border border-neutral-800 bg-neutral-950 p-3">
              <h3 className="mb-2 text-xs text-neutral-500">关注/持仓池</h3>
              {data.watchlist.length === 0 ? (
                <p className="text-sm text-neutral-500">暂无 A 股关注标的</p>
              ) : (
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-1">
                  {data.watchlist.slice(0, 8).map((item) => <WatchRow key={item.symbol} item={item} />)}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
