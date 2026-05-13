'use client'

import useSWR from 'swr'

import { fetchHealth } from '@/lib/api'
import type { Market } from '@/lib/types'
import { MarketCard } from '@/components/MarketCard'
import { TopMoversCard } from '@/components/TopMoversCard'
import { SectorHeatmap } from '@/components/SectorHeatmap'
import { IndexCard } from '@/components/IndexCard'

const INDICES: string[] = ['000001.SH', '399001.SZ', 'HSI.HK', 'HSTECH.HK']
const MARKETS: Market[] = ['ashare', 'hk', 'us', 'crypto']

export default function MarketPage() {
  const { data: health } = useSWR('health', fetchHealth, { refreshInterval: 15_000 })
  return (
    <main className="p-6 max-w-7xl mx-auto space-y-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">市场</h1>
        <span className="text-xs text-neutral-500">
          {health ? `状态:${health.status}` : '载入中'}
        </span>
      </header>

      {/* 大盘指数 (4 个大 cell:富途风格) */}
      <section>
        <h2 className="text-sm text-neutral-400 mb-2">大盘指数</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {INDICES.map((sym) => <IndexCard key={sym} symbol={sym} />)}
        </div>
      </section>

      {/* 行业热力图 */}
      <SectorHeatmap />

      {/* 全市场 TOP */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <TopMoversCard market="ashare" />
        <TopMoversCard market="hk" />
      </div>

      {/* 原四市场卡片(保留作 universe 快速概览,可折叠) */}
      <details className="rounded-lg border border-neutral-800 bg-neutral-950">
        <summary className="cursor-pointer px-4 py-2 text-sm text-neutral-400 hover:text-neutral-200">
          四市场 universe 快速概览
        </summary>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4">
          {MARKETS.map((m) => (
            <MarketCard key={m} market={m} health={health?.adapters[m]} />
          ))}
        </div>
      </details>
    </main>
  )
}
