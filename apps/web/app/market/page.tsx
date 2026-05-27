'use client'

import useSWR from 'swr'

import { fetchHealth } from '@/lib/api'
import { IntradayRadarPanel } from '@/components/IntradayRadarPanel'
import { MarketPulsePanel } from '@/components/MarketPulsePanel'
import { RealtimeSectorsPanel } from '@/components/RealtimeSectorsPanel'

export default function MarketPage() {
  const { data: health } = useSWR('health', fetchHealth, { refreshInterval: 15_000 })
  return (
    <main className="p-6 max-w-7xl mx-auto space-y-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">市场</h1>
        <div className="flex items-center gap-3">
          <a href="/ai-market" className="text-xs text-blue-400 hover:text-blue-300">AI 盘面</a>
          <span className="text-xs text-neutral-500">
            {health ? `状态:${health.status}` : '载入中'}
          </span>
        </div>
      </header>

      <MarketPulsePanel />
      <IntradayRadarPanel />
      <RealtimeSectorsPanel />
    </main>
  )
}
