'use client'

import type { CSSProperties } from 'react'
import Link from 'next/link'

import { useLiveMessages } from '@/lib/use_live_messages'
import type { Market } from '@/lib/types'

function fmtTime(value: string): string {
  return new Date(value).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit',
  })
}

// 重点提示条: 从实盘消息只挑高优先级 (warning/critical: 炸板风险/龙头回落/题材轮动/宽度恶化)。
// 非全量刷屏 —— 完整消息流去盘后回放。
export function KeyAlertsStrip({ market }: { market: Market }) {
  const { messages } = useLiveMessages(market, 50)
  if (market !== 'ashare') return null
  const alerts = messages.filter((m) => m.level === 'warning' || m.level === 'critical').slice(0, 6)
  if (alerts.length === 0) return null

  return (
    <div style={st.strip}>
      <span style={st.tag}>⚠ 重点提示</span>
      <div style={st.list}>
        {alerts.map((m) => (
          <span key={m.id} style={{ ...st.item, ...(m.level === 'critical' ? st.critical : st.warning) }}>
            <span style={st.time}>{fmtTime(m.ts)}</span>
            {m.title}
          </span>
        ))}
      </div>
      <Link href="/replay" style={st.more}>盘后回放 →</Link>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  strip: { display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', border: '1px solid var(--border)', borderRadius: 10, background: 'var(--bg2)', flexWrap: 'wrap' },
  tag: { fontSize: 12, fontWeight: 700, color: '#d97706', whiteSpace: 'nowrap' },
  list: { display: 'flex', gap: 8, flexWrap: 'wrap', flex: 1 },
  item: { display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, padding: '2px 8px', borderRadius: 6, border: '1px solid' },
  warning: { color: '#d97706', borderColor: 'rgba(217,119,6,0.3)', background: 'rgba(217,119,6,0.06)' },
  critical: { color: '#dc2626', borderColor: 'rgba(220,38,38,0.3)', background: 'rgba(220,38,38,0.06)' },
  time: { fontFamily: 'monospace', fontSize: 11, opacity: 0.8 },
  more: { color: 'var(--accent)', fontSize: 12, fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap' },
}
