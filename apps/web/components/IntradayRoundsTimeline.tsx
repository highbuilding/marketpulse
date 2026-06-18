'use client'

import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchIntradayRounds, type RoundSlot } from '@/lib/market_changes_api'

function SlotCard({ slot }: { slot: RoundSlot }) {
  const active = slot.phase === 'active'
  const market = slot.sections.find((s) => s.key === 'market_state')
  const theme = slot.sections.find((s) => s.key === 'theme_state')
  const rest = slot.sections.filter((s) => s.key !== 'market_state' && s.key !== 'theme_state')
  return (
    <div style={{ ...st.slot, ...(active ? st.slotActive : {}) }}>
      <div style={st.slotHead}>
        <span style={st.slotTime}>{slot.slot_time}</span>
        {active
          ? <span style={st.activeBadge}><span style={st.pulse} />进行态</span>
          : <span style={st.cooledBadge}>冷却态</span>}
      </div>
      {market && (
        <div style={st.section}>
          <span style={st.secLabel}>大盘 · {market.label}</span>
          <span style={st.secSummary}>{market.summary}</span>
        </div>
      )}
      {theme && (
        <div style={st.section}>
          <span style={st.secLabel}>题材 · {theme.label}</span>
          <span style={st.secSummary}>{theme.summary}</span>
        </div>
      )}
      {rest.length > 0 && (
        <div style={st.chips}>
          {rest.map((s) => <span key={s.key} style={st.chip}>{s.title}: {s.label}</span>)}
        </div>
      )}
    </div>
  )
}

// 30min 结论轮: 当前进行态 + 历史冷却态。复用结论层 (大盘/题材/涨停/风险/信号) section。
export function IntradayRoundsTimeline({ market }: { market: string }) {
  const { data, error, isLoading } = useSWR(
    market === 'ashare' ? ['intraday-rounds', market] : null,
    () => fetchIntradayRounds(market),
    { refreshInterval: 30_000 },
  )
  if (market !== 'ashare') {
    return (
      <section className="panel">
        <div className="panel-header">30 分钟走势轮</div>
        <div style={st.empty}>仅支持 A 股。</div>
      </section>
    )
  }
  // 最新在前 (含进行态)。
  const slots = [...(data?.slots ?? [])].reverse()
  return (
    <section className="panel">
      <div className="panel-header">
        30 分钟走势轮
        <span style={st.headerMeta}>大盘 + 板块题材走势</span>
      </div>
      <div style={st.wrap}>
        {error && <div style={st.empty}>加载失败</div>}
        {isLoading && slots.length === 0 && <div style={st.empty}>加载中</div>}
        {!isLoading && slots.length === 0 && <div style={st.empty}>盘中每 30 分钟生成一轮结论</div>}
        {slots.map((s) => <SlotCard key={`${s.slot_time}-${s.phase}`} slot={s} />)}
      </div>
    </section>
  )
}

const st: Record<string, CSSProperties> = {
  headerMeta: { marginLeft: 'auto', color: 'var(--text3)', fontSize: 12, fontWeight: 400 },
  wrap: { display: 'grid', gap: 8, padding: 12, maxHeight: 480, overflow: 'auto' },
  slot: { border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg2)', padding: '10px 12px' },
  slotActive: { borderColor: 'rgba(99,102,241,0.45)', background: 'var(--accent-bg)' },
  slotHead: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 },
  slotTime: { fontFamily: 'monospace', fontWeight: 700, fontSize: 13 },
  activeBadge: { display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--accent)', fontSize: 11, fontWeight: 700, border: '1px solid rgba(99,102,241,0.4)', borderRadius: 5, padding: '1px 6px' },
  pulse: { width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)' },
  cooledBadge: { color: 'var(--text3)', fontSize: 11, border: '1px solid var(--border)', borderRadius: 5, padding: '1px 6px' },
  section: { display: 'grid', gap: 1, marginBottom: 4 },
  secLabel: { fontSize: 12, fontWeight: 700 },
  secSummary: { fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 },
  chips: { display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 },
  chip: { fontSize: 11, color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 5, padding: '1px 6px', background: 'var(--bg3)' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center' },
}
