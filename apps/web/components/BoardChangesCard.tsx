'use client'

import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchBoardChanges } from '@/lib/market_changes_api'

function fmtYi(v?: number | null): string {
  if (v == null) return '—'
  return `${(v / 1e8).toFixed(2)}亿`
}

export function BoardChangesCard({ market }: { market: string }) {
  const { data, error, isLoading } = useSWR(
    market === 'ashare' ? ['board-changes', market] : null,
    () => fetchBoardChanges(market),
    { refreshInterval: 30_000 },
  )
  if (market !== 'ashare') {
    return (
      <section className="panel">
        <div className="panel-header">板块异动</div>
        <div style={st.empty}>板块异动仅支持 A 股。</div>
      </section>
    )
  }
  const boards = data?.boards ?? []
  const stale = data?.meta?.stale
  return (
    <section className="panel">
      <div className="panel-header">
        板块异动
        <span style={st.headerMeta}>{stale ? '盘中刷新' : `${boards.length} 板块`}</span>
      </div>
      <div style={st.wrap}>
        {error && <div style={st.empty}>加载失败</div>}
        {isLoading && boards.length === 0 && <div style={st.empty}>加载中</div>}
        {!isLoading && boards.length === 0 && <div style={st.empty}>暂无板块异动</div>}
        {boards.map((b) => (
          <div key={b.board_name} style={st.row}>
            <span style={st.board}>{b.board_name}</span>
            {b.change_pct != null && (
              <span style={{ ...st.pct, color: b.change_pct >= 0 ? '#dc2626' : '#16a34a' }}>
                {b.change_pct >= 0 ? '+' : ''}{b.change_pct.toFixed(2)}%
              </span>
            )}
            <span style={st.flow}>主力 {fmtYi(b.main_net_inflow)}</span>
            <span style={st.cnt}>异动 {b.change_total ?? 0}</span>
            {b.top_name && (
              <span style={st.top} title={`最频繁异动: ${b.top_name} (${b.top_direction ?? ''})`}>
                {b.top_name}
              </span>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

const st: Record<string, CSSProperties> = {
  headerMeta: { marginLeft: 'auto', color: 'var(--text3)', fontSize: 12, fontWeight: 400 },
  wrap: { display: 'grid', gap: 4, padding: 12, maxHeight: 360, overflow: 'auto' },
  row: { display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px', borderRadius: 6, background: 'var(--bg2)', fontSize: 12 },
  board: { fontWeight: 600, minWidth: 96 },
  pct: { fontFamily: 'monospace', fontWeight: 700, minWidth: 64, textAlign: 'right' },
  flow: { color: 'var(--text3)', fontSize: 11, minWidth: 90 },
  cnt: { color: 'var(--text3)', fontSize: 11, minWidth: 64 },
  top: { fontSize: 11, marginLeft: 'auto' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center' },
}
