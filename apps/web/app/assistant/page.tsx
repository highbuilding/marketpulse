'use client'

import { type CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchLiveMessageAiContext } from '@/lib/live_messages_api'
import { useMarket } from '@/lib/market-context'

/**
 * AI 助手页。持仓已挪到概览页(自选下方, PositionsPanel)。
 * 本页保留 AI 结论 / 通知候选骨架, 待后续阶段接入。
 */
export default function AssistantPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const { data, isLoading } = useSWR(
    isAshare ? ['live-message-ai-context', market] : null,
    () => fetchLiveMessageAiContext(market, 30),
    { refreshInterval: 60_000 },
  )
  const messages = data?.messages ?? []

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
            持仓管理已移至<b>概览页</b>(自选下方)。题材雷达、候选机会和 AI 结论会在后续阶段接入。
          </p>
        </div>
      </section>

      <section style={styles.grid}>
        <div style={styles.panel}>
          <h2 style={styles.panelTitle}>AI 结论</h2>
          <p style={styles.panelDesc}>
            第一版先沉淀可复盘的实盘消息事实源,AI 结论会基于右侧上下文生成。
          </p>
          <div style={styles.emptyState}>等待 AI 摘要生成服务接入</div>
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
                  <span>{new Date(m.ts).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</span>
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
  messageList: { display: 'grid', gap: 8, marginTop: 12 },
  message: { border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--bg)' },
  messageTop: { display: 'flex', gap: 8, color: 'var(--text3)', fontSize: 11, marginBottom: 4 },
}
