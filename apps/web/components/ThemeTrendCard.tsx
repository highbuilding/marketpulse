'use client'

import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchIntradayRounds } from '@/lib/market_changes_api'

interface TopTheme {
  theme_name?: string
  score?: number
  momentum?: number
  up_ratio?: number
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

// 题材走势: 复用结论层 theme_state 的 top_themes 榜 (最新一轮)。
// 复用 30min 轮的 SWR key, 与 IntradayRoundsTimeline 共享请求 (dedup)。
export function ThemeTrendCard({ market }: { market: string }) {
  const { data, isLoading } = useSWR(
    market === 'ashare' ? ['intraday-rounds', market] : null,
    () => fetchIntradayRounds(market),
    { refreshInterval: 30_000 },
  )
  if (market !== 'ashare') {
    return (
      <section className="panel">
        <div className="panel-header">题材走势</div>
        <div style={st.empty}>仅支持 A 股。</div>
      </section>
    )
  }
  const slots = data?.slots ?? []
  const latest = slots[slots.length - 1]
  const theme = latest?.sections.find((s) => s.key === 'theme_state')
  const topThemes = (theme?.evidence?.top_themes as TopTheme[] | undefined) ?? []

  return (
    <section className="panel">
      <div className="panel-header">
        题材走势
        {theme && <span style={st.headerMeta}>{theme.label}</span>}
      </div>
      <div style={st.wrap}>
        {isLoading && !data && <div style={st.empty}>加载中</div>}
        {!isLoading && topThemes.length === 0 && <div style={st.empty}>盘中题材快照未就绪</div>}
        {theme && <div style={st.summary}>{theme.summary}</div>}
        {topThemes.map((t, i) => {
          const score = num(t.score)
          const mom = num(t.momentum)
          return (
            <div key={`${t.theme_name}-${i}`} style={st.row}>
              <span style={st.rank}>{i + 1}</span>
              <span style={st.name}>{t.theme_name ?? '—'}</span>
              {score != null && (
                <span style={st.score}>热度 {score.toFixed(0)}</span>
              )}
              {mom != null && (
                <span style={{ ...st.mom, color: mom >= 0 ? '#dc2626' : '#16a34a' }}>
                  {mom >= 0 ? '↑' : '↓'} {Math.abs(mom).toFixed(2)}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

const st: Record<string, CSSProperties> = {
  headerMeta: { marginLeft: 'auto', color: 'var(--accent)', fontSize: 12, fontWeight: 600 },
  wrap: { display: 'grid', gap: 4, padding: 12 },
  summary: { fontSize: 12, color: 'var(--text2)', lineHeight: 1.5, marginBottom: 4 },
  row: { display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px', borderRadius: 6, background: 'var(--bg2)', fontSize: 12 },
  rank: { color: 'var(--text3)', fontFamily: 'monospace', width: 18 },
  name: { fontWeight: 600, flex: 1 },
  score: { color: 'var(--text3)', fontSize: 11 },
  mom: { fontFamily: 'monospace', fontWeight: 700, minWidth: 52, textAlign: 'right' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center' },
}
