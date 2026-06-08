'use client'

import { type CSSProperties } from 'react'

import { useMarket } from '@/lib/market-context'

/**
 * AI 助手页。持仓已挪到概览页(自选下方, PositionsPanel)。
 * 本页保留 AI 结论 / 通知候选骨架, 待后续阶段接入。
 */
export default function AssistantPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'

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
            等 Phase 6 接入后，这里展示盘面状态、开单观察、风险和持仓建议。
          </p>
          <div style={styles.emptyState}>等待 AITradeOpinionService 接入</div>
        </div>
        <div style={styles.panel}>
          <h2 style={styles.panelTitle}>通知候选</h2>
          <p style={styles.panelDesc}>
            系统会先生成可通知结论和冷却状态，外部渠道暂不发送。
          </p>
          <div style={styles.emptyState}>等待通知能力骨架接入</div>
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
}
