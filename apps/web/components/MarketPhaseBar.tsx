'use client'

import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'

import { ashareBoardPhase, ashareBoardPhaseLabel, type AshareBoardPhase } from '@/lib/markets'

// 盘面顶部时段条: 高亮当前 A 股阶段 + 实时时钟。驱动下方模块的视觉重心。
const PHASES: AshareBoardPhase[] = ['auction', 'open', 'intraday', 'closing', 'post']

export function MarketPhaseBar() {
  const [now, setNow] = useState<Date>(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15_000)
    return () => clearInterval(t)
  }, [])

  const phase = ashareBoardPhase(now)
  const clock = now.toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  const isClosed = phase === 'closed' || phase === 'pre' || phase === 'post'

  return (
    <div style={st.bar}>
      <div style={st.clock}>
        <span style={st.clockTime}>{clock}</span>
        <span style={st.clockTz}>BJT</span>
      </div>
      <div style={st.phases}>
        {PHASES.map((p) => {
          const active = p === phase
          return (
            <span key={p} style={{ ...st.chip, ...(active ? st.chipActive : {}) }}>
              {active && <span style={st.dot} />}
              {ashareBoardPhaseLabel(p)}
            </span>
          )
        })}
      </div>
      {isClosed && <span style={st.closed}>{ashareBoardPhaseLabel(phase)} · 数据为最近交易日</span>}
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  bar: { display: 'flex', alignItems: 'center', gap: 16, padding: '10px 14px', border: '1px solid var(--border)', borderRadius: 10, background: 'var(--bg2)', flexWrap: 'wrap' },
  clock: { display: 'flex', alignItems: 'baseline', gap: 5 },
  clockTime: { fontSize: 18, fontWeight: 700, fontFamily: 'monospace', letterSpacing: 0.5 },
  clockTz: { fontSize: 11, color: 'var(--text3)' },
  phases: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  chip: { display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 14, fontSize: 12, color: 'var(--text3)', border: '1px solid var(--border)', background: 'var(--bg3)' },
  chipActive: { color: 'var(--accent)', borderColor: 'rgba(99,102,241,0.4)', background: 'var(--accent-bg)', fontWeight: 700 },
  dot: { width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)' },
  closed: { marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' },
}
