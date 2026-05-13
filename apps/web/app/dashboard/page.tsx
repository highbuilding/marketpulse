'use client'

import useSWR from 'swr'

import { fetchHealth } from '@/lib/api'
import type { Market } from '@/lib/types'
import { MarketCard } from '@/components/MarketCard'

const MARKETS: Market[] = ['ashare', 'hk', 'us', 'crypto']

export default function DashboardPage() {
  const { data: health } = useSWR('health', fetchHealth, { refreshInterval: 15_000 })
  return (
    <main className="p-6 max-w-7xl mx-auto">
      <header className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">MarketPulse · Dashboard</h1>
        <span className="text-xs text-neutral-500">
          {health ? `状态:${health.status}` : '载入中'}
        </span>
      </header>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {MARKETS.map((m) => (
          <MarketCard key={m} market={m} health={health?.adapters[m]} />
        ))}
      </div>
    </main>
  )
}
