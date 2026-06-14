'use client'

import { type CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchLiveMessageAiContext } from '@/lib/live_messages_api'
import { useMarket } from '@/lib/market-context'
import type { LiveMessage } from '@/lib/types'

function fmtTime(value: string): string {
  return new Date(value).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
  })
}

function uniqTitles(messages: LiveMessage[], category: string): string[] {
  const seen = new Set<string>()
  return messages
    .filter((m) => m.category === category)
    .map((m) => m.title)
    .filter((title) => {
      if (seen.has(title)) return false
      seen.add(title)
      return true
    })
    .slice(0, 4)
}

export default function AssistantPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const { data, isLoading } = useSWR(
    isAshare ? ['live-message-ai-context', market] : null,
    () => fetchLiveMessageAiContext(market, 30),
    { refreshInterval: 60_000 },
  )
  const messages = data?.messages ?? []
  const themeTitles = uniqTitles(messages, 'theme')
  const watchTitles = uniqTitles(messages, 'watchlist')
  const signalTitles = uniqTitles(messages, 'signal')
  const riskTitles = messages
    .filter((m) => m.level === 'warning' || m.level === 'critical' || m.category === 'risk')
    .map((m) => m.title)
    .slice(0, 4)
  const latest = messages[0]

  if (!isAshare) {
    return (
      <main style={styles.page}>
        <section style={styles.header}>
          <div>
            <div style={styles.eyebrow}>AI 助手</div>
            <h1 style={styles.title}>{marketLabel.name}暂未接入 AI 题材决策</h1>
            <p style={styles.subtle}>
              当前 AI 盘中决策助手第一版只支持 A 股。其他市场后续会按各自交易结构单独设计。
            </p>
          </div>
        </section>
      </main>
    )
  }

  return (
    <main style={styles.page}>
      <section style={styles.header}>
        <div>
          <div style={styles.eyebrow}>A 股 AI 助手</div>
          <h1 style={styles.title}>盘中结论</h1>
          <p style={styles.subtle}>
            基于实盘消息事实源汇总题材、自选股、CD 信号和风险点。
          </p>
        </div>
      </section>

      <section style={styles.grid}>
        <div style={styles.panel}>
          <h2 style={styles.panelTitle}>盘中摘要</h2>
          <p style={styles.panelDesc}>
            最近 30 分钟消息 {messages.length} 条
            {latest ? `,最新 ${fmtTime(latest.ts)}` : ''}
          </p>
          {messages.length === 0 ? (
            <div style={styles.emptyState}>暂无实盘消息。开盘后由行情事件自动生成摘要。</div>
          ) : (
            <div style={styles.summaryGrid}>
              <SummaryBlock title="题材主线" items={themeTitles} empty="暂无题材消息" />
              <SummaryBlock title="自选异动" items={watchTitles} empty="暂无自选消息" />
              <SummaryBlock title="CD 信号" items={signalTitles} empty="暂无 CD 信号" />
              <SummaryBlock title="风险点" items={riskTitles} empty="暂无风险消息" />
            </div>
          )}
        </div>
        <div style={styles.panel}>
          <h2 style={styles.panelTitle}>实盘上下文</h2>
          <p style={styles.panelDesc}>
            最近 30 分钟由行情事件、题材库、自选股和 CD 信号生成的可复盘消息。
          </p>
          <div style={styles.messageList}>
            {isLoading && messages.length === 0 && <div style={styles.emptyState}>加载中</div>}
            {!isLoading && messages.length === 0 && <div style={styles.emptyState}>暂无实盘消息</div>}
            {messages.slice(0, 8).map((m) => (
              <article key={m.id} style={styles.message}>
                <div style={styles.messageTop}>
                  <span>{fmtTime(m.ts)}</span>
                  <span>{m.category}</span>
                  <span>{m.level}</span>
                </div>
                <b>{m.title}</b>
                <p>{m.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>
    </main>
  )
}

function SummaryBlock({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <section style={styles.summaryBlock}>
      <h3 style={styles.summaryTitle}>{title}</h3>
      {items.length === 0 ? (
        <p style={styles.summaryEmpty}>{empty}</p>
      ) : (
        <ul style={styles.summaryList}>
          {items.map((item) => <li key={item}>{item}</li>)}
        </ul>
      )}
    </section>
  )
}

const styles: Record<string, CSSProperties> = {
  page: { padding: 24, maxWidth: 1280, margin: '0 auto' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, marginBottom: 20 },
  eyebrow: { color: 'var(--accent)', fontSize: 12, fontWeight: 700, marginBottom: 6 },
  title: { margin: 0, fontSize: 24, fontWeight: 700 },
  subtle: { color: 'var(--text2)', fontSize: 14, margin: '8px 0 0', lineHeight: 1.6 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 },
  panel: { border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg2)', padding: 16 },
  panelTitle: { margin: 0, fontSize: 16, fontWeight: 700 },
  panelDesc: { margin: '6px 0 0', color: 'var(--text3)', fontSize: 13, lineHeight: 1.5 },
  emptyState: { marginTop: 12, border: '1px dashed var(--border)', borderRadius: 8, padding: 16, color: 'var(--text3)', fontSize: 13, textAlign: 'center' },
  summaryGrid: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10, marginTop: 12 },
  summaryBlock: { border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--bg)' },
  summaryTitle: { margin: '0 0 8px', fontSize: 13, fontWeight: 700 },
  summaryEmpty: { margin: 0, color: 'var(--text3)', fontSize: 12 },
  summaryList: { margin: 0, paddingLeft: 18, color: 'var(--text2)', fontSize: 12, lineHeight: 1.7 },
  messageList: { display: 'grid', gap: 8, marginTop: 12 },
  message: { border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--bg)' },
  messageTop: { display: 'flex', gap: 8, color: 'var(--text3)', fontSize: 11, marginBottom: 4 },
}
