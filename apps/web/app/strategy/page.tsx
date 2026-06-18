'use client'

import Link from 'next/link'
import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { useMarket } from '@/lib/market-context'
import { fetchStrategyBacktests, fetchTradeInstructions } from '@/lib/strategy_api'
import type { StrategyRun, TradeInstruction } from '@/lib/types'

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return '--'
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function num(v: number | null | undefined, digits = 1): string {
  if (v == null) return '--'
  return v.toFixed(digits)
}

function statusLabel(s: StrategyRun): { text: string; color: string } {
  if (s.sandbox_eligible) return { text: '可进沙盒', color: 'var(--green)' }
  if (s.status === 'insufficient_data') return { text: '数据不足', color: 'var(--gold)' }
  if (s.status === 'watch_only') return { text: '仅观察', color: 'var(--text2)' }
  return { text: s.status, color: 'var(--text3)' }
}

export default function StrategyPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const { data, error, isLoading } = useSWR(
    isAshare ? ['strategy-backtests', market] : null,
    () => fetchStrategyBacktests(market),
    { refreshInterval: 60_000 },
  )
  const { data: instructions } = useSWR(
    isAshare ? ['strategy-instructions', market] : null,
    () => fetchTradeInstructions(market),
    { refreshInterval: 60_000 },
  )

  if (!isAshare) {
    return (
      <div style={st.page}>
        <h1 style={st.h1}>策略回测</h1>
        <p style={st.muted}>交易大脑第一版仅支持 A 股,当前为 {marketLabel.name}。</p>
      </div>
    )
  }

  const items = data?.items ?? []
  const activeInstructions = instructions?.items ?? []

  return (
    <div style={st.page}>
      <header style={st.header}>
        <div>
          <h1 style={st.h1}>策略回测 · 交易大脑</h1>
          <p style={st.muted}>真实回测报告来自 collector 盘后任务,不是演示数据。</p>
        </div>
        <div style={st.meta}>
          <span>策略 {items.length}</span>
          <span>纸面指令 {activeInstructions.length}</span>
        </div>
      </header>

      {isLoading && <p style={st.muted}>加载中…</p>}
      {error && <p style={st.warn}>加载失败: {String(error)}</p>}
      {data?.meta?.stale && (
        <section style={st.warnBox}>
          暂无回测报告。等待 A 股 collector 收盘任务运行,或手动触发策略回测任务后这里会显示真实结果。
        </section>
      )}

      {activeInstructions.length > 0 && (
        <section style={st.section}>
          <h2 style={st.h2}>纸面交易指令</h2>
          <div style={st.instructionGrid}>
            {activeInstructions.map((item) => <InstructionCard key={item.instruction_key} item={item} />)}
          </div>
        </section>
      )}

      <section style={st.section}>
        <h2 style={st.h2}>策略表现</h2>
        <div style={st.grid}>
          {items.map((s) => {
            const label = statusLabel(s)
            return (
              <Link key={s.strategy_id} href={`/strategy/${s.strategy_id}`} style={st.card}>
                <div style={st.cardHead}>
                  <b style={st.cardTitle}>{s.strategy_name}</b>
                  <span style={{ ...st.status, color: label.color }}>{label.text}</span>
                </div>
                <p style={st.desc}>{s.description}</p>
                <div style={st.metrics}>
                  <Metric label="胜率" value={pct(s.win_rate)} />
                  <Metric label="均值收益" value={pct(s.avg_return_pct, 2)} />
                  <Metric label="最大回撤" value={pct(s.max_drawdown_pct, 2)} down />
                  <Metric label="交易数" value={String(s.trade_count)} />
                </div>
                <div style={st.foot}>
                  <span>{s.horizon}</span>
                  <span>{s.sample_start ?? '--'} → {s.sample_end ?? '--'}</span>
                </div>
              </Link>
            )
          })}
        </div>
      </section>

      <section style={st.section}>
        <h2 style={st.h2}>系统环节</h2>
        <div style={st.flow}>
          {['采集数据', '事实标准化', '每日复盘/盘中结论', '策略条件', 'vectorbt 回测', '沙盒指令', '结果追踪'].map((x) => (
            <div key={x} style={st.flowItem}>{x}</div>
          ))}
        </div>
        <p style={st.muted}>
          复盘条件会被结构化为市场门控、题材门控和个股状态矩阵。vectorbt 只消费这些矩阵,不读取自然语言结论。
        </p>
      </section>
    </div>
  )
}

function Metric({ label, value, down = false }: { label: string; value: string; down?: boolean }) {
  return (
    <div>
      <div style={st.metricLabel}>{label}</div>
      <div className={down ? 'text-down font-mono' : 'font-mono'} style={st.metricValue}>{value}</div>
    </div>
  )
}

function InstructionCard({ item }: { item: TradeInstruction }) {
  return (
    <div style={st.instruction}>
      <div style={st.cardHead}>
        <b>{item.title}</b>
        <span style={st.action}>{item.action}</span>
      </div>
      <p style={st.desc}>{item.summary}</p>
      <div style={st.foot}>
        <span>{item.target_name ?? item.target_id}</span>
        <span>置信度 {num(item.confidence * 100)}%</span>
      </div>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  page: { padding: 24, maxWidth: 1180, margin: '0 auto' },
  header: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 18 },
  h1: { fontSize: 20, margin: 0 },
  h2: { fontSize: 15, margin: '0 0 12px' },
  muted: { color: 'var(--text3)', fontSize: 13, margin: '6px 0 0' },
  warn: { color: 'var(--red)', fontSize: 13 },
  warnBox: { border: '1px solid rgba(217,119,6,0.35)', background: 'rgba(217,119,6,0.08)', color: 'var(--gold)', borderRadius: 8, padding: 12, marginBottom: 18 },
  meta: { display: 'flex', gap: 8, color: 'var(--text2)', fontSize: 12, whiteSpace: 'nowrap' },
  section: { marginTop: 18 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 },
  instructionGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 },
  card: { background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8, padding: 16, color: 'inherit', textDecoration: 'none', display: 'block' },
  instruction: { background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8, padding: 14 },
  cardHead: { display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', marginBottom: 8 },
  cardTitle: { fontSize: 15 },
  status: { fontSize: 12, whiteSpace: 'nowrap' },
  action: { fontSize: 11, color: 'var(--accent)', background: 'var(--accent-bg)', padding: '2px 7px', borderRadius: 5 },
  desc: { color: 'var(--text3)', fontSize: 12, minHeight: 36, margin: '0 0 12px' },
  metrics: { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 12 },
  metricLabel: { fontSize: 11, color: 'var(--text3)' },
  metricValue: { fontSize: 15, marginTop: 2 },
  foot: { display: 'flex', justifyContent: 'space-between', gap: 10, color: 'var(--text3)', fontSize: 11 },
  flow: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8, marginBottom: 10 },
  flowItem: { border: '1px solid var(--border)', background: 'var(--bg2)', borderRadius: 6, padding: '9px 10px', textAlign: 'center', fontSize: 12 },
}
