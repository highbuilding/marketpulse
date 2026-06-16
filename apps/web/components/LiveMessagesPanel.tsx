'use client'

import type { CSSProperties } from 'react'

import Link from 'next/link'
import useSWR from 'swr'

import { fetchLiveMessageStatus } from '@/lib/live_messages_api'
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

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function symbolPct(value: unknown): string | null {
  const obj = record(value)
  const symbol = typeof obj?.symbol === 'string' ? obj.symbol : null
  const change = num(obj?.change_pct)
  if (!symbol || change === null) return null
  return `${symbol} ${change.toFixed(2)}%`
}

function evidenceItems(message: LiveMessage): string[] {
  const p = message.payload
  const items: string[] = []
  const memberCount = num(p.member_count)
  const knownCount = num(p.known_count)
  if (memberCount !== null && knownCount !== null) {
    items.push(`报价覆盖 ${knownCount}/${memberCount}`)
  }
  const upCount = num(p.up_count)
  const downCount = num(p.down_count)
  if (upCount !== null || downCount !== null) {
    items.push(`上涨 ${upCount ?? 0} / 下跌 ${downCount ?? 0}`)
  }
  const coreCount = num(p.core_count)
  const coreUpCount = num(p.core_up_count)
  if (coreCount !== null && coreUpCount !== null) {
    items.push(`核心上涨 ${coreUpCount}/${coreCount}`)
  }
  const avg = num(p.avg_change_pct)
  if (avg !== null) items.push(`均值 ${avg.toFixed(2)}%`)
  const leader = symbolPct(p.leader)
  if (leader) items.push(`领涨 ${leader}`)
  const laggard = symbolPct(p.laggard)
  if (laggard) items.push(`拖累 ${laggard}`)
  const change = num(p.change_pct)
  const prev = num(p.prev_change_pct)
  if (change !== null) items.push(`涨跌幅 ${change.toFixed(2)}%`)
  if (prev !== null) items.push(`前值 ${prev.toFixed(2)}%`)
  const amount = num(p.amount)
  if (amount !== null) items.push(`成交额 ${(amount / 100000000).toFixed(2)}亿`)
  const interval = typeof p.interval === 'string' ? p.interval : null
  const signalType = typeof p.signal_type === 'string' ? p.signal_type : null
  const price = num(p.price)
  if (interval) items.push(`周期 ${interval}`)
  if (signalType) items.push(`信号 ${signalType === 'buy' ? '买入' : signalType === 'sell' ? '卖出' : signalType}`)
  if (price !== null && message.category === 'signal') items.push(`价格 ${price.toFixed(2)}`)
  return items
}

function MessageRow({ message }: { message: LiveMessage }) {
  const evidence = evidenceItems(message)
  return (
    <article style={st.row}>
      <div style={st.rowTop}>
        <span style={st.time}>{fmtTime(message.ts)}</span>
        <span style={{ ...st.badge, ...levelStyle(message.level) }}>{levelLabel[message.level]}</span>
        <span style={st.category}>{categoryLabel[message.category]}</span>
      </div>
      <div style={st.title}>{message.title}</div>
      <div style={st.body}>{message.body}</div>
      {evidence.length > 0 && (
        <div style={st.evidence}>
          {evidence.slice(0, 6).map((item) => <span key={item} style={st.evidenceItem}>{item}</span>)}
        </div>
      )}
      {message.symbols.length > 0 && (
        <div style={st.symbols}>{message.symbols.slice(0, 5).join(' / ')}</div>
      )}
    </article>
  )
}

export function LiveMessagesPanel({ limit = 30 }: { limit?: number }) {
  const { market } = useMarket()
  const { messages, error, isLoading } = useLiveMessages(market, limit)
  const { data: status } = useSWR(
    market === 'ashare' ? ['live-message-status', market] : null,
    () => fetchLiveMessageStatus(market),
    { refreshInterval: 60_000 },
  )
  const isAshare = market === 'ashare'
  const headerMeta = isAshare && status
    ? `最近 ${messages.length}/${limit} 条 · 监听 ${status.enabled_themes} 题材 / ${status.enabled_constituents} 成分股 / ${status.watchlist_symbols} 自选`
    : (isAshare ? `最近 ${messages.length}/${limit} 条` : '仅 A 股')

  return (
    <section className="panel">
      <div className="panel-header">
        实盘消息
        <span style={st.headerMeta}>{headerMeta}</span>
        {isAshare && <Link href="/replay" style={st.replayLink}>盘后回放</Link>}
      </div>
      <div style={st.wrap}>
        {!isAshare && <div style={st.empty}>实盘消息第一版仅支持 A 股。</div>}
        {isAshare && status && (
          <div style={st.status}>
            <span>规则 {status.rule_version}</span>
            <span>{status.latest_message_ts ? `最近 ${fmtTime(status.latest_message_ts)} ${status.latest_message_title ?? ''}` : '监听范围已就绪，等待行情事件'}</span>
          </div>
        )}
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
  replayLink: { color: 'var(--accent)', fontSize: 12, fontWeight: 600, textDecoration: 'none' },
  wrap: { display: 'grid', gap: 8, padding: 12, maxHeight: 420, overflow: 'auto' },
  row: { border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg2)', padding: '10px 12px' },
  rowTop: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 },
  time: { color: 'var(--text3)', fontSize: 11, fontFamily: 'monospace' },
  badge: { border: '1px solid', borderRadius: 5, padding: '1px 6px', fontSize: 11, fontWeight: 600 },
  category: { color: 'var(--text3)', fontSize: 11 },
  title: { fontSize: 13, fontWeight: 700, marginBottom: 3 },
  body: { color: 'var(--text2)', fontSize: 12, lineHeight: 1.5 },
  symbols: { marginTop: 6, color: 'var(--text3)', fontSize: 11, fontFamily: 'monospace' },
  evidence: { display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 },
  evidenceItem: { color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 5, padding: '2px 6px', fontSize: 11, background: 'var(--bg3)' },
  status: { display: 'flex', justifyContent: 'space-between', gap: 10, color: 'var(--text3)', fontSize: 12, border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg2)', padding: '8px 10px' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center' },
  err: { color: '#dc2626', fontSize: 13, padding: 12, border: '1px solid rgba(220,38,38,0.2)', borderRadius: 8, background: 'rgba(220,38,38,0.08)' },
}
