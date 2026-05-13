'use client'

import { useState } from 'react'
import useSWR from 'swr'

import { KLineChart } from '@/components/KLineChart'
import { FundFlowPanel } from '@/components/FundFlowPanel'
import { fetchBars, fetchSymbolProfile } from '@/lib/symbol_api'
import type { Interval } from '@/lib/types'

const INTERVALS: { key: Interval; label: string }[] = [
  { key: '1d', label: '日线' },
  { key: '1wk', label: '周线' },
  { key: '1mo', label: '月线' },
  { key: '60m', label: '60分' },
  { key: '15m', label: '15分' },
  { key: '5m', label: '5分' },
]

export default function SymbolPage({ params }: { params: { code: string } }) {
  const symbol = decodeURIComponent(params.code)
  const [interval, setInterval] = useState<Interval>('1d')

  const { data: profile } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))

  const isIntraday = ['1m', '5m', '15m', '30m', '60m'].includes(interval)
  const { data, error, isLoading } = useSWR(
    `bars:${symbol}:${interval}`,
    () => fetchBars(symbol, interval, isIntraday ? 5 : 365),
    { refreshInterval: 60_000 },
  )

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-4">
          <h1 className="text-2xl font-bold">{profile?.name ?? '—'}</h1>
          <span className="text-lg font-mono text-neutral-400">{symbol}</span>
        </div>
        <a href="/market" className="text-xs text-neutral-400 hover:text-neutral-200">← 市场</a>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <div className="flex gap-1 mb-3">
          {INTERVALS.map((iv) => (
            <button
              key={iv.key}
              onClick={() => setInterval(iv.key)}
              className={`px-2 py-1 text-xs rounded ${interval === iv.key ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'}`}
            >
              {iv.label}
            </button>
          ))}
        </div>
        {isLoading && <p className="text-sm text-neutral-500">加载 K 线…</p>}
        {error && <p className="text-sm text-red-400">加载失败:{String(error)}</p>}
        {data && data.bars.length > 0 && <KLineChart bars={data.bars} height={420} />}
        {data && data.bars.length === 0 && (
          <p className="text-sm text-yellow-400">无数据。请先 <code>make warmup</code> 或者本周期还未抓取。</p>
        )}
      </section>

      <FundFlowPanel symbol={symbol} />
    </main>
  )
}
