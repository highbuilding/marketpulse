'use client'

import useSWR from 'swr'
import { useMarket } from '@/lib/market-context'
import { fetchHealth } from '@/lib/api'
import { LiveMessagesPanel } from '@/components/LiveMessagesPanel'

export default function MarketPage() {
  const { market, marketLabel } = useMarket()
  const { data: health } = useSWR('health', fetchHealth, { refreshInterval: 30_000 })

  return (
    <div style={{ padding: 20 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>
          {marketLabel.flag} {marketLabel.name} 行情
        </h1>
        <p style={{ color: 'var(--text2)' }}>
          {marketLabel.sub} · 实时行情监控
          {health && <span style={{ marginLeft: 12, fontSize: 11, color: 'var(--text3)' }}>API: {health.status}</span>}
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
        <LiveMessagesPanel />
      </div>
    </div>
  )
}
