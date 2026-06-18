'use client'

import { useState } from 'react'
import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchChanges, type ChangeRow } from '@/lib/market_changes_api'
import { SymbolLink } from './SymbolLink'

// 异动类型分组 tab。category 对应后端 CHANGE_CATEGORY。
const TABS: { key: string; label: string; cats: string[] }[] = [
  { key: 'all', label: '全部', cats: [] },
  { key: 'surge', label: '快速拉升', cats: ['surge'] },
  { key: 'plunge', label: '快速跳水', cats: ['plunge'] },
  { key: 'limit_up', label: '封涨停', cats: ['limit_up'] },
  { key: 'broken', label: '炸板', cats: ['broken'] },
  // 竞价异动(开盘一次性)归 AuctionPerformanceCard, 不在实时异动流里(缓存只留最近200条会被挤出)。
]

const UP_CATS = new Set(['surge', 'limit_up', 'auction_up'])

function pctColor(row: ChangeRow): string {
  if (row.category && UP_CATS.has(row.category)) return 'var(--up, #dc2626)'
  if (row.change_pct != null) return row.change_pct >= 0 ? 'var(--up, #dc2626)' : 'var(--down, #16a34a)'
  return 'var(--text2)'
}

export function ChangesFeed({ market }: { market: string }) {
  const [tab, setTab] = useState('all')
  const cats = TABS.find((t) => t.key === tab)?.cats ?? []
  const { data, error, isLoading } = useSWR(
    market === 'ashare' ? ['changes', market, tab] : null,
    () => fetchChanges(market, cats),
    { refreshInterval: 12_000 },
  )

  if (market !== 'ashare') {
    return (
      <section className="panel">
        <div className="panel-header">个股异动</div>
        <div style={st.empty}>异动监控仅支持 A 股。</div>
      </section>
    )
  }

  const rows = data?.changes ?? []
  const stale = data?.meta?.stale
  const counts = data?.counts ?? {}

  return (
    <section className="panel">
      <div className="panel-header">
        个股异动
        <span style={st.headerMeta}>
          {stale ? '暂无数据(盘中刷新)' : `${rows.length} 条`}
        </span>
      </div>
      <div style={st.tabs}>
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{ ...st.tab, ...(tab === t.key ? st.tabActive : {}) }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div style={st.wrap}>
        {error && <div style={st.empty}>加载失败</div>}
        {isLoading && rows.length === 0 && <div style={st.empty}>加载中</div>}
        {!isLoading && rows.length === 0 && (
          <div style={st.empty}>{stale ? '盘中开始监控异动' : '当前分类暂无异动'}</div>
        )}
        {rows.map((r, i) => (
          <div key={`${r.symbol}-${r.time}-${i}`} style={st.row}>
            <span style={st.time}>{r.time}</span>
            <span style={st.type}>{r.change_type}</span>
            <SymbolLink symbol={r.symbol} name={r.name} collected={r.collected} style={st.name} />
            {r.price != null && <span style={st.price}>{r.price.toFixed(2)}</span>}
            {r.change_pct != null && (
              <span style={{ ...st.pct, color: pctColor(r) }}>
                {r.change_pct >= 0 ? '+' : ''}{r.change_pct.toFixed(2)}%
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
  tabs: { display: 'flex', gap: 6, padding: '8px 12px 0', flexWrap: 'wrap' },
  tab: { padding: '3px 10px', borderRadius: 6, fontSize: 12, color: 'var(--text2)', border: '1px solid var(--border)', background: 'var(--bg3)', cursor: 'pointer' },
  tabActive: { color: 'var(--accent)', borderColor: 'rgba(99,102,241,0.4)', background: 'var(--accent-bg)', fontWeight: 600 },
  wrap: { display: 'grid', gap: 4, padding: 12, maxHeight: 440, overflow: 'auto' },
  row: { display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px', borderRadius: 6, background: 'var(--bg2)', fontSize: 12 },
  time: { color: 'var(--text3)', fontFamily: 'monospace', fontSize: 11, minWidth: 56 },
  type: { color: 'var(--text3)', fontSize: 11, minWidth: 56 },
  name: { fontWeight: 600, flex: 1 },
  price: { fontFamily: 'monospace', color: 'var(--text2)', minWidth: 56, textAlign: 'right' },
  pct: { fontFamily: 'monospace', fontWeight: 700, minWidth: 64, textAlign: 'right' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center' },
}
