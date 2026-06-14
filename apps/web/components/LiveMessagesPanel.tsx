'use client'

import type { CSSProperties } from 'react'

import { useMarket } from '@/lib/market-context'
import { useLiveMessages } from '@/lib/use_live_messages'
import type { LiveMessage, LiveMessageCategory, LiveMessageLevel } from '@/lib/types'

const levelLabel: Record<LiveMessageLevel, string> = {
  info: '信息',
  watch: '观察',
  warning: '风险',
  critical: '重要',
}

const categoryLabel: Record<LiveMessageCategory, string> = {
  index: '指数',
  theme: '题材',
  watchlist: '自选',
  signal: '信号',
  risk: '风险',
  system: '系统',
}

function fmtTime(value: string): string {
  return new Date(value).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function levelStyle(level: LiveMessageLevel): CSSProperties {
  if (level === 'critical') return { color: '#dc2626', borderColor: 'rgba(220,38,38,0.35)', background: 'rgba(220,38,38,0.08)' }
  if (level === 'warning') return { color: '#d97706', borderColor: 'rgba(217,119,6,0.35)', background: 'rgba(217,119,6,0.08)' }
  if (level === 'watch') return { color: 'var(--accent)', borderColor: 'rgba(99,102,241,0.35)', background: 'var(--accent-bg)' }
  return { color: 'var(--text2)', borderColor: 'var(--border)', background: 'var(--bg3)' }
}

function MessageRow({ message }: { message: LiveMessage }) {
  return (
    <article style={st.row}>
      <div style={st.rowTop}>
        <span style={st.time}>{fmtTime(message.ts)}</span>
        <span style={{ ...st.badge, ...levelStyle(message.level) }}>{levelLabel[message.level]}</span>
        <span style={st.category}>{categoryLabel[message.category]}</span>
      </div>
      <div style={st.title}>{message.title}</div>
      <div style={st.body}>{message.body}</div>
      {message.symbols.length > 0 && (
        <div style={st.symbols}>{message.symbols.slice(0, 5).join(' / ')}</div>
      )}
    </article>
  )
}

export function LiveMessagesPanel({ limit = 30 }: { limit?: number }) {
  const { market } = useMarket()
  const { messages, error, isLoading } = useLiveMessages(market, limit)
  const isAshare = market === 'ashare'

  return (
    <section className="panel">
      <div className="panel-header">
        实盘消息
        <span style={st.headerMeta}>{isAshare ? `${messages.length} 条` : '仅 A 股'}</span>
      </div>
      <div style={st.wrap}>
        {!isAshare && <div style={st.empty}>实盘消息第一版仅支持 A 股。</div>}
        {isAshare && error && <div style={st.err}>实盘消息加载失败</div>}
        {isAshare && isLoading && messages.length === 0 && <div style={st.empty}>加载中</div>}
        {isAshare && !isLoading && messages.length === 0 && (
          <div style={st.empty}>暂无实盘消息。开盘后行情事件触发规则时会自动出现。</div>
        )}
        {messages.map((message) => <MessageRow key={message.id} message={message} />)}
      </div>
    </section>
  )
}

const st: Record<string, CSSProperties> = {
  headerMeta: { marginLeft: 'auto', color: 'var(--text3)', fontSize: 12, fontWeight: 400 },
  wrap: { display: 'grid', gap: 8, padding: 12, maxHeight: 420, overflow: 'auto' },
  row: { border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg2)', padding: '10px 12px' },
  rowTop: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 },
  time: { color: 'var(--text3)', fontSize: 11, fontFamily: 'monospace' },
  badge: { border: '1px solid', borderRadius: 5, padding: '1px 6px', fontSize: 11, fontWeight: 600 },
  category: { color: 'var(--text3)', fontSize: 11 },
  title: { fontSize: 13, fontWeight: 700, marginBottom: 3 },
  body: { color: 'var(--text2)', fontSize: 12, lineHeight: 1.5 },
  symbols: { marginTop: 6, color: 'var(--text3)', fontSize: 11, fontFamily: 'monospace' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center' },
  err: { color: '#dc2626', fontSize: 13, padding: 12, border: '1px solid rgba(220,38,38,0.2)', borderRadius: 8, background: 'rgba(220,38,38,0.08)' },
}

