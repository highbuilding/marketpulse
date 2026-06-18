'use client'

import type { CSSProperties } from 'react'
import Link from 'next/link'
import useSWR from 'swr'

import { fetchAIMarketPacket } from '@/lib/api'

// 大盘指数条: 复用 AI 大盘数据包的 indices (collector 预热, 零 ak_call)。
export function IndexBar({ market }: { market: string }) {
  const { data } = useSWR(
    market === 'ashare' ? 'ai-packet-indices' : null,
    fetchAIMarketPacket,
    { refreshInterval: 60_000 },
  )
  if (market !== 'ashare') return null
  const indices = data?.indices ?? []
  if (indices.length === 0) return null
  return (
    <div style={st.bar}>
      {indices.map((idx) => {
        const pct = idx.change_pct
        const color = pct == null ? 'var(--text2)' : pct >= 0 ? '#dc2626' : '#16a34a'
        return (
          <Link key={idx.symbol} href={`/symbol/${encodeURIComponent(idx.symbol)}`}
                style={st.cell} title={idx.symbol}>
            <span style={st.name}>{idx.name ?? idx.symbol}</span>
            <span style={{ ...st.price, color }}>{idx.price?.toFixed(2) ?? '—'}</span>
            <span style={{ ...st.pct, color }}>
              {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
            </span>
          </Link>
        )
      })}
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  bar: { display: 'flex', gap: 8, padding: 10, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--bg2)', overflowX: 'auto' },
  cell: { display: 'grid', gap: 1, minWidth: 96, padding: '4px 10px', borderRadius: 8, background: 'var(--bg3)', textDecoration: 'none', color: 'inherit' },
  name: { fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' },
  price: { fontSize: 14, fontWeight: 700, fontFamily: 'monospace' },
  pct: { fontSize: 12, fontWeight: 600, fontFamily: 'monospace' },
}
