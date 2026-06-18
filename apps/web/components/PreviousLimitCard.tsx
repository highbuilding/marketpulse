'use client'

import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchLimitPool } from '@/lib/market_changes_api'
import { SymbolLink } from './SymbolLink'

// 昨日涨停今日表现: 看昨涨停股今日是延续(红)还是退潮(绿), 衡量市场赚钱效应。
export function PreviousLimitCard({ market }: { market: string }) {
  const { data, error, isLoading } = useSWR(
    market === 'ashare' ? ['limit-pool', market, 'previous'] : null,
    () => fetchLimitPool(market, 'previous'),
    { refreshInterval: 30_000 },
  )
  if (market !== 'ashare') {
    return (
      <section className="panel">
        <div className="panel-header">昨日涨停今日表现</div>
        <div style={st.empty}>仅支持 A 股。</div>
      </section>
    )
  }
  const items = data?.items ?? []
  const up = items.filter((i) => (i.change_pct ?? 0) > 0).length
  const flat = items.filter((i) => (i.change_pct ?? 0) === 0).length
  const down = items.length - up - flat
  const avg = items.length
    ? items.reduce((s, i) => s + (i.change_pct ?? 0), 0) / items.length
    : null

  return (
    <section className="panel">
      <div className="panel-header">
        昨日涨停今日表现
        {items.length > 0 && (
          <span style={st.headerMeta}>
            {items.length} 只 · 均 {avg != null ? `${avg >= 0 ? '+' : ''}${avg.toFixed(2)}%` : '—'}
          </span>
        )}
      </div>
      {items.length > 0 && (
        <div style={st.dist}>
          <span style={{ color: '#dc2626' }}>红盘 {up}</span>
          <span style={{ color: 'var(--text3)' }}>平 {flat}</span>
          <span style={{ color: '#16a34a' }}>绿盘 {down}</span>
        </div>
      )}
      <div style={st.wrap}>
        {error && <div style={st.empty}>加载失败</div>}
        {isLoading && items.length === 0 && <div style={st.empty}>加载中</div>}
        {!isLoading && items.length === 0 && <div style={st.empty}>昨日无涨停或数据未就绪</div>}
        {items.map((it) => (
          <div key={it.symbol} style={st.row}>
            <SymbolLink symbol={it.symbol} name={it.name} collected={it.collected} style={st.name} />
            {it.ladder_count != null && it.ladder_count > 0 && (
              <span style={st.ladder}>{it.ladder_count}连板</span>
            )}
            {it.industry && <span style={st.ind}>{it.industry}</span>}
            {it.change_pct != null && (
              <span style={{ ...st.pct, color: it.change_pct >= 0 ? '#dc2626' : '#16a34a' }}>
                {it.change_pct >= 0 ? '+' : ''}{it.change_pct.toFixed(2)}%
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
  dist: { display: 'flex', gap: 16, padding: '8px 12px', fontSize: 12, fontWeight: 600, borderBottom: '1px solid var(--border)' },
  wrap: { display: 'grid', gap: 4, padding: 12, maxHeight: 400, overflow: 'auto' },
  row: { display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px', borderRadius: 6, background: 'var(--bg2)', fontSize: 12 },
  name: { fontWeight: 600, flex: 1 },
  ladder: { color: '#d97706', fontSize: 11, fontWeight: 600 },
  ind: { color: 'var(--text3)', fontSize: 11 },
  pct: { fontFamily: 'monospace', fontWeight: 700, minWidth: 64, textAlign: 'right' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center' },
}
