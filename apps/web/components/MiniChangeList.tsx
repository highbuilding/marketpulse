'use client'

import type { CSSProperties } from 'react'

import type { ChangeRow, LimitRow } from '@/lib/market_changes_api'
import { SymbolLink } from './SymbolLink'

type Row = ChangeRow | LimitRow

// 竞价/尾盘卡共用的一列迷你榜单。up=红色基调, down=绿色基调。
export function MiniChangeList({
  title, rows, tone, empty = '暂无',
}: {
  title: string
  rows: Row[]
  tone: 'up' | 'down' | 'neutral'
  empty?: string
}) {
  const color = tone === 'up' ? '#dc2626' : tone === 'down' ? '#16a34a' : 'var(--text2)'
  return (
    <div style={st.col}>
      <div style={{ ...st.title, color }}>{title} <span style={st.cnt}>{rows.length}</span></div>
      {rows.length === 0 && <div style={st.empty}>{empty}</div>}
      {rows.slice(0, 12).map((r, i) => (
        <div key={`${r.symbol}-${i}`} style={st.row}>
          <SymbolLink symbol={r.symbol} name={r.name} collected={r.collected} style={st.name} />
          {r.change_pct != null && (
            <span style={{ ...st.pct, color: r.change_pct >= 0 ? '#dc2626' : '#16a34a' }}>
              {r.change_pct >= 0 ? '+' : ''}{r.change_pct.toFixed(2)}%
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  col: { flex: 1, minWidth: 150, display: 'grid', gap: 3, alignContent: 'start' },
  title: { fontSize: 12, fontWeight: 700, marginBottom: 2 },
  cnt: { color: 'var(--text3)', fontWeight: 400, fontSize: 11 },
  row: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px', borderRadius: 5, background: 'var(--bg2)', fontSize: 12 },
  name: { flex: 1, fontWeight: 600 },
  pct: { fontFamily: 'monospace', fontWeight: 700, fontSize: 11 },
  empty: { color: 'var(--text3)', fontSize: 12, padding: '6px 4px' },
}
