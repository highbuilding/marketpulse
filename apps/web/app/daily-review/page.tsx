'use client'

import { type CSSProperties, useEffect, useState } from 'react'
import useSWR from 'swr'

import { useMarket } from '@/lib/market-context'
import { fetchDailyReview, fetchDailyReviewDates } from '@/lib/conclusions_api'
import type {
  ConclusionSection,
  DailyReviewResponse,
  LeaderGroup,
  LeaderItem,
  SectorRow,
} from '@/lib/types'

function todayBjt(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
}

function pct(v: number | null | undefined): string {
  if (v == null) return '--'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function posLabel(r: number | null | undefined): string {
  if (r == null) return '--'
  if (r < 0.3) return `${(r * 100).toFixed(0)}% 低位`
  if (r >= 0.6) return `${(r * 100).toFixed(0)}% 高位`
  return `${(r * 100).toFixed(0)}% 中位`
}

const chgColor = (v: number | null | undefined): string =>
  v == null ? 'var(--text3)' : v > 0 ? '#ef4444' : v < 0 ? '#22c55e' : 'var(--text2)'

export default function DailyReviewPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const [date, setDate] = useState(todayBjt())

  const { data: dates } = useSWR(
    isAshare ? ['daily-review-dates', market] : null,
    () => fetchDailyReviewDates(market),
    { revalidateOnFocus: false },
  )
  const { data, isLoading, error } = useSWR<DailyReviewResponse>(
    isAshare ? ['daily-review', market, date] : null,
    () => fetchDailyReview(market, date),
    { revalidateOnFocus: false },
  )

  useEffect(() => {
    // 若有已生成的交易日且当前 date 不在其中, 自动跳到最近一个
    if (dates && dates.length > 0 && !dates.includes(date)) {
      setDate(dates[0])
    }
  }, [dates]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!isAshare) {
    return (
      <div style={st.page}>
        <h1 style={st.h1}>每日复盘</h1>
        <p style={st.muted}>每日复盘目前仅支持 A 股,当前为 {marketLabel.name}。</p>
      </div>
    )
  }

  const sections = data?.sections ?? []
  const byKey = (k: string) => sections.find((s) => s.key === k)
  const trend = byKey('market_trend')
  const sectorPos = byKey('sector_position')
  const leaders = byKey('sector_leaders')
  const msgSections = sections.filter(
    (s) => !['market_trend', 'sector_position', 'sector_leaders'].includes(s.key),
  )

  return (
    <div style={st.page}>
      <header style={st.header}>
        <h1 style={st.h1}>每日复盘 · {marketLabel.name}</h1>
        <div style={st.controls}>
          <input
            type="date"
            value={date}
            max={todayBjt()}
            onChange={(e) => setDate(e.target.value)}
            style={st.dateInput}
          />
          {dates && dates.length > 0 && (
            <select
              value={dates.includes(date) ? date : ''}
              onChange={(e) => e.target.value && setDate(e.target.value)}
              style={st.select}
            >
              <option value="">已生成日期…</option>
              {dates.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          )}
        </div>
      </header>

      {isLoading && <p style={st.muted}>加载中…</p>}
      {error && <p style={st.muted}>加载失败: {String(error)}</p>}

      {data && (
        <>
          {data.summary && <div style={st.summary}>{data.summary}</div>}

          {trend && <TrendCard section={trend} />}
          {sectorPos && <SectorPositionCard section={sectorPos} />}
          {leaders && <LeadersCard section={leaders} />}

          {msgSections.length > 0 && (
            <section style={st.card}>
              <h2 style={st.h2}>盘中事实层</h2>
              <div style={st.msgGrid}>
                {msgSections.map((s) => (
                  <div key={s.key} style={st.msgItem}>
                    <div style={st.msgTitle}>{s.title}</div>
                    <div style={st.msgLabel}>{s.label}</div>
                    <div style={st.msgBody}>{s.summary}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {data.next_watch.length > 0 && (
            <section style={st.card}>
              <h2 style={st.h2}>次日观察</h2>
              <ul style={st.watchList}>
                {data.next_watch.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </section>
          )}

          {data.data_gaps.length > 0 && (
            <section style={st.gaps}>
              <strong>数据缺口</strong>
              <ul style={st.gapList}>
                {data.data_gaps.map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  )
}

function TrendCard({ section }: { section: ConclusionSection }) {
  const indices = (section.evidence?.indices as Array<Record<string, number | string>>) ?? []
  return (
    <section style={st.card}>
      <div style={st.cardHead}>
        <h2 style={st.h2}>{section.title}</h2>
        <span style={st.badge}>{section.label}</span>
      </div>
      <p style={st.cardSummary}>{section.summary}</p>
      <table style={st.table}>
        <thead>
          <tr>
            <th style={st.th}>指数</th>
            <th style={st.thR}>当日</th>
            <th style={st.thR}>年内</th>
            <th style={st.thR}>年内位置</th>
            <th style={st.thR}>距年内高</th>
            <th style={st.thR}>最大回撤</th>
          </tr>
        </thead>
        <tbody>
          {indices.map((r) => (
            <tr key={String(r.symbol)}>
              <td style={st.td}>{String(r.name)}</td>
              <td style={{ ...st.tdR, color: chgColor(r.day_change_pct as number) }}>{pct(r.day_change_pct as number)}</td>
              <td style={{ ...st.tdR, color: chgColor(r.ytd_change_pct as number) }}>{pct(r.ytd_change_pct as number)}</td>
              <td style={st.tdR}>{posLabel(r.position_ratio as number)}</td>
              <td style={st.tdR}>{pct(r.from_high_pct as number)}</td>
              <td style={{ ...st.tdR, color: chgColor(r.max_drawdown_pct as number) }}>{pct(r.max_drawdown_pct as number)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function SectorPositionCard({ section }: { section: ConclusionSection }) {
  const core = (section.evidence?.core_sectors as SectorRow[]) ?? []
  const low = (section.evidence?.low_sectors as SectorRow[]) ?? []
  return (
    <section style={st.card}>
      <div style={st.cardHead}>
        <h2 style={st.h2}>{section.title}</h2>
        <span style={st.badge}>{section.label}</span>
      </div>
      <div style={st.twoCol}>
        <SectorTable title="🔥 核心板块" rows={core} metric="momentum" />
        <SectorTable title="🧊 低位板块" rows={low} metric="ytd" />
      </div>
    </section>
  )
}

function SectorTable({ title, rows, metric }: { title: string; rows: SectorRow[]; metric: 'momentum' | 'ytd' }) {
  return (
    <div style={st.col}>
      <h3 style={st.h3}>{title}</h3>
      <table style={st.table}>
        <thead>
          <tr>
            <th style={st.th}>板块</th>
            <th style={st.thR}>年内位置</th>
            <th style={st.thR}>{metric === 'momentum' ? '20日动量' : '年内涨幅'}</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && <tr><td style={st.td} colSpan={3}>暂无</td></tr>}
          {rows.map((r) => (
            <tr key={r.key}>
              <td style={st.td}>{r.name}{r.kind === 'theme' ? ' ·题材' : ''}</td>
              <td style={st.tdR}>{posLabel(r.position_ratio)}</td>
              <td style={{ ...st.tdR, color: chgColor(metric === 'momentum' ? r.momentum_20d_pct : r.ytd_change_pct) }}>
                {pct(metric === 'momentum' ? r.momentum_20d_pct : r.ytd_change_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LeadersCard({ section }: { section: ConclusionSection }) {
  const groups = (section.evidence?.groups as LeaderGroup[]) ?? []
  const names = (xs: LeaderItem[]) => xs.map((x) => x.name || x.symbol).join('、') || '—'
  return (
    <section style={st.card}>
      <div style={st.cardHead}>
        <h2 style={st.h2}>{section.title}</h2>
        <span style={st.badge}>{section.label}</span>
      </div>
      <div style={st.leaderGrid}>
        {groups.map((g) => (
          <div key={g.theme_code} style={st.leaderGroup}>
            <div style={st.leaderTheme}>{g.theme_name}</div>
            <div style={st.tier}><span style={{ ...st.tierTag, background: '#7f1d1d' }}>龙头</span>{names(g.leaders)}</div>
            <div style={st.tier}><span style={{ ...st.tierTag, background: '#78350f' }}>中军</span>{names(g.mid)}</div>
            <div style={st.tier}><span style={{ ...st.tierTag, background: '#374151' }}>杂毛</span>{names(g.laggards)}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

const st: Record<string, CSSProperties> = {
  page: { padding: '20px 24px', maxWidth: 1100, margin: '0 auto' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 },
  h1: { fontSize: 20, fontWeight: 600, margin: 0 },
  h2: { fontSize: 15, fontWeight: 600, margin: 0 },
  h3: { fontSize: 13, fontWeight: 600, margin: '0 0 8px', color: 'var(--text2)' },
  controls: { display: 'flex', gap: 8, alignItems: 'center' },
  dateInput: { background: 'var(--bg2)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px' },
  select: { background: 'var(--bg2)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px' },
  muted: { color: 'var(--text3)' },
  summary: { background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px', marginBottom: 16, lineHeight: 1.6, fontSize: 14 },
  card: { background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8, padding: 16, marginBottom: 16 },
  cardHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  cardSummary: { color: 'var(--text2)', fontSize: 13, margin: '0 0 12px', lineHeight: 1.5 },
  badge: { fontSize: 12, padding: '2px 10px', borderRadius: 12, background: 'var(--bg3)', color: 'var(--text2)' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '6px 8px', color: 'var(--text3)', fontWeight: 500, borderBottom: '1px solid var(--border)' },
  thR: { textAlign: 'right', padding: '6px 8px', color: 'var(--text3)', fontWeight: 500, borderBottom: '1px solid var(--border)' },
  td: { padding: '6px 8px', borderBottom: '1px solid var(--border)' },
  tdR: { padding: '6px 8px', textAlign: 'right', borderBottom: '1px solid var(--border)' },
  twoCol: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 },
  col: {},
  leaderGrid: { display: 'flex', flexDirection: 'column', gap: 12 },
  leaderGroup: { padding: '10px 0', borderBottom: '1px solid var(--border)' },
  leaderTheme: { fontWeight: 600, marginBottom: 6 },
  tier: { display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 13, marginBottom: 4, lineHeight: 1.5 },
  tierTag: { fontSize: 11, padding: '1px 8px', borderRadius: 4, color: '#fff', flexShrink: 0 },
  msgGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 },
  msgItem: { background: 'var(--bg3)', borderRadius: 6, padding: 12 },
  msgTitle: { fontSize: 13, fontWeight: 600 },
  msgLabel: { fontSize: 12, color: 'var(--accent)', margin: '2px 0 6px' },
  msgBody: { fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 },
  watchList: { margin: 0, paddingLeft: 20, lineHeight: 1.8, fontSize: 13 },
  gaps: { background: 'var(--bg2)', border: '1px dashed var(--border)', borderRadius: 8, padding: '12px 16px', color: 'var(--text3)', fontSize: 12 },
  gapList: { margin: '6px 0 0', paddingLeft: 20, lineHeight: 1.6 },
}
