'use client'

import useSWR from 'swr'

import { fetchAIMarketPacket } from '@/lib/api'
import type { AIMarketSector, AIMarketSymbol } from '@/lib/types'

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

function StockPill({ item }: { item: AIMarketSymbol }) {
  return (
    <a
      href={`/symbol/${encodeURIComponent(item.symbol)}`}
      className="flex min-w-0 items-center justify-between gap-2 rounded border border-neutral-800 bg-neutral-900/70 px-2 py-1 hover:border-neutral-600"
    >
      <span className="min-w-0 truncate">
        <span className="font-mono text-[11px] text-neutral-500">{item.symbol}</span>
        <span className="ml-1 text-xs text-neutral-300">{item.name ?? '—'}</span>
      </span>
      <span className={`shrink-0 text-xs tabular-nums ${pctClass(item.change_pct)}`}>
        {fmtPct(item.change_pct)}
      </span>
    </a>
  )
}

function SectorCard({ sector }: { sector: AIMarketSector }) {
  const stocks = sector.constituents ?? []
  return (
    <article className="rounded border border-neutral-800 bg-neutral-950 p-3">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <h3 className="truncate text-sm font-medium text-neutral-100">{sector.name}</h3>
            <span className="shrink-0 text-xs text-neutral-500">{sector.code}</span>
          </div>
          <p className="mt-1 truncate text-xs text-neutral-500">
            领涨：{sector.leader_name || '—'} {fmtPct(sector.leader_change_pct)}
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            扩散：{sector.breadth_label}
            {sector.up_ratio != null ? ` · 成分上涨 ${Math.round(sector.up_ratio * 100)}%` : ''}
          </p>
        </div>
        <div className={`shrink-0 text-right text-lg font-semibold tabular-nums ${pctClass(sector.change_pct)}`}>
          {fmtPct(sector.change_pct)}
        </div>
      </header>

      <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-1.5">
        {stocks.length === 0 ? (
          <p className="text-xs text-neutral-500">成分股暂缺</p>
        ) : stocks.slice(0, 6).map((item) => (
          <StockPill key={item.symbol} item={item} />
        ))}
      </div>
    </article>
  )
}

export function RealtimeSectorsPanel() {
  const { data, error, isLoading } = useSWR('ai-market-packet', fetchAIMarketPacket, {
    refreshInterval: 60_000,
  })

  return (
    <section className="space-y-3">
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <h2 className="text-sm text-neutral-400">板块 / 行业 / 题材表现</h2>
          <p className="mt-1 text-xs text-neutral-500">
            AKShare 行业/概念板块口径，展示涨跌幅、领涨股、成分股和扩散状态；自动刷新 60 秒。
          </p>
        </div>
        <span className="text-xs text-neutral-500">
          全 A 成交额 {fmtAmount(data?.breadth.total_amount)}
        </span>
      </header>

      {isLoading && <div className="rounded border border-neutral-800 bg-neutral-950 p-4 text-sm text-neutral-500">加载中</div>}
      {error && <div className="rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">板块数据加载失败</div>}
      {data?.degraded.length ? (
        <div className="rounded border border-yellow-900/70 bg-yellow-950/20 p-3 text-sm text-yellow-200">
          数据降级：{data.degraded.join('；')}
        </div>
      ) : null}

      {data && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="space-y-2">
            <h3 className="text-xs text-red-400">热门板块</h3>
            {data.hot_sectors.length === 0 ? (
              <p className="rounded border border-neutral-800 bg-neutral-950 p-3 text-sm text-neutral-500">暂无板块数据</p>
            ) : data.hot_sectors.slice(0, 6).map((sector) => (
              <SectorCard key={sector.code} sector={sector} />
            ))}
          </div>
          <div className="space-y-2">
            <h3 className="text-xs text-green-400">弱势板块</h3>
            {data.weak_sectors.length === 0 ? (
              <p className="rounded border border-neutral-800 bg-neutral-950 p-3 text-sm text-neutral-500">暂无板块数据</p>
            ) : data.weak_sectors.slice(0, 6).map((sector) => (
              <SectorCard key={sector.code} sector={sector} />
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
