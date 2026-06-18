'use client'

import Link from 'next/link'
import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { useMarket } from '@/lib/market-context'
import { fetchStrategyReport } from '@/lib/strategy_api'
import type { StrategyRun } from '@/lib/types'

function pct(v: unknown, digits = 2): string {
  if (typeof v !== 'number' || Number.isNaN(v)) return '--'
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function text(v: unknown): string {
  if (v == null || v === '') return '--'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2)
  return String(v)
}

function EquityCurve({ points }: { points: Array<{ date: string; value: number }> }) {
  if (points.length < 2) return <div style={st.empty}>暂无资金曲线</div>
  const W = 760, H = 220, pad = 10
  const values = points.map((p) => p.value)
  const min = Math.min(...values), max = Math.max(...values)
  const span = max - min || 1
  const path = points.map((p, i) => {
    const x = pad + (i / (points.length - 1)) * (W - pad * 2)
    const y = pad + (1 - (p.value - min) / span) * (H - pad * 2)
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 220, display: 'block' }}>
      <path d={`${path} L${W - pad},${H - pad} L${pad},${H - pad} Z`} fill="var(--accent-bg)" opacity={0.55} />
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} />
    </svg>
  )
}

export default function StrategyReport({ params }: { params: { id: string } }) {
  const { market } = useMarket()
  const { id } = params
  const { data, error, isLoading } = useSWR(
    market === 'ashare' ? ['strategy-report', market, id] : null,
    () => fetchStrategyReport(market, id),
    { refreshInterval: 60_000 },
  )
  const s = data?.strategy

  return (
    <div style={st.page}>
      <Link href="/strategy" style={st.back}>返回策略列表</Link>
      {isLoading && <p style={st.muted}>加载中…</p>}
      {error && <p style={st.warn}>加载失败: {String(error)}</p>}
      {s && (
        <>
          <header style={st.header}>
            <div>
              <h1 style={st.h1}>{s.strategy_name}</h1>
              <p style={st.muted}>{s.description}</p>
            </div>
            <div style={st.badge}>{s.sandbox_eligible ? '可进沙盒' : s.status}</div>
          </header>

          <section style={st.panel}>
            <h2 style={st.h2}>绩效摘要</h2>
            <div style={st.metrics}>
              <Metric label="胜率" value={pct(s.win_rate, 1)} />
              <Metric label="均值收益" value={pct(s.avg_return_pct)} />
              <Metric label="中位数收益" value={pct(s.median_return_pct)} />
              <Metric label="最大回撤" value={pct(s.max_drawdown_pct)} down />
              <Metric label="最差单笔" value={pct(s.worst_trade_pct)} down />
              <Metric label="平均持有" value={`${text(s.avg_holding_days)} 日`} />
              <Metric label="交易数" value={String(s.trade_count)} />
              <Metric label="股票数" value={String(s.symbol_count)} />
            </div>
          </section>

          <section style={st.panel}>
            <h2 style={st.h2}>资金曲线</h2>
            <EquityCurve points={s.equity_curve} />
          </section>

          <section style={st.twoCol}>
            <TablePanel title="1/3/5/10 日收益" rows={returnsRows(s)} cols={['窗口', '样本', '胜率', '均值收益', '中位数']} />
            <TablePanel title="市场状态表现" rows={stateRows(s.market_state)} cols={['状态', '交易', '胜率', '均值收益']} />
          </section>

          <section style={st.twoCol}>
            <TablePanel title="题材强弱表现" rows={stateRows(s.theme_state)} cols={['状态', '交易', '胜率', '均值收益']} />
            <TablePanel title="卖出规则对比" rows={exitRows(s)} cols={['规则', '交易', '胜率', '均值收益']} />
          </section>

          <section style={st.panel}>
            <h2 style={st.h2}>规则说明</h2>
            <div style={st.ruleGrid}>
              <Info label="买入规则" value={text(s.metrics.entry_rule)} />
              <Info label="卖出规则" value={text(s.metrics.exit_rule)} />
              <Info label="执行口径" value={text(s.metrics.execution)} />
              <Info label="引擎" value={`${s.engine} · ${s.sample_start ?? '--'} → ${s.sample_end ?? '--'}`} />
            </div>
          </section>

          {s.data_gaps.length > 0 && (
            <section style={st.gaps}>
              <b>数据缺口</b>
              <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                {s.data_gaps.map((g) => <li key={g}>{g}</li>)}
              </ul>
            </section>
          )}

          <section style={st.panel}>
            <h2 style={st.h2}>交易明细</h2>
            <table className="data-table">
              <thead>
                <tr><th>标的</th><th>买入日</th><th>卖出日</th><th>买入价</th><th>卖出价</th><th>持有</th><th>收益</th><th>原因</th></tr>
              </thead>
              <tbody>
                {(data?.trades ?? []).map((t) => (
                  <tr key={`${t.symbol}-${t.entry_date}-${t.exit_date}`} style={{ cursor: 'default' }}>
                    <td className="font-mono">{t.symbol}</td>
                    <td className="font-mono">{t.entry_date}</td>
                    <td className="font-mono">{t.exit_date}</td>
                    <td className="font-mono">{t.entry_price.toFixed(2)}</td>
                    <td className="font-mono">{t.exit_price.toFixed(2)}</td>
                    <td className="font-mono">{t.holding_days}</td>
                    <td className={`font-mono ${t.return_pct >= 0 ? 'text-up' : 'text-down'}`}>{pct(t.return_pct)}</td>
                    <td>{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
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

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={st.metricLabel}>{label}</div>
      <div style={st.infoValue}>{value}</div>
    </div>
  )
}

function TablePanel({ title, rows, cols }: { title: string; rows: string[][]; cols: string[] }) {
  return (
    <section style={st.panel}>
      <h2 style={st.h2}>{title}</h2>
      <table className="data-table">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={cols.length}>暂无</td></tr>}
          {rows.map((r, i) => (
            <tr key={i} style={{ cursor: 'default' }}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function returnsRows(s: StrategyRun): string[][] {
  return Object.entries(s.returns ?? {}).map(([k, v]) => [
    k,
    String(v.count ?? 0),
    pct(v.win_rate, 1),
    pct(v.avg_return_pct),
    pct(v.median_return_pct),
  ])
}

function stateRows(rows: Array<Record<string, unknown>>): string[][] {
  return rows.map((r) => [
    text(r.state),
    text(r.trade_count),
    pct(r.win_rate, 1),
    pct(r.avg_return_pct),
  ])
}

function exitRows(s: StrategyRun): string[][] {
  return (s.exit_comparison ?? []).map((r) => [
    text(r.exit_rule),
    text(r.trade_count),
    pct(r.win_rate, 1),
    pct(r.avg_return_pct),
  ])
}

const st: Record<string, CSSProperties> = {
  page: { padding: 24, maxWidth: 1080, margin: '0 auto' },
  back: { color: 'var(--text3)', textDecoration: 'none', fontSize: 13 },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, margin: '10px 0 18px' },
  h1: { fontSize: 20, margin: '0 0 4px' },
  h2: { fontSize: 15, margin: '0 0 12px' },
  muted: { color: 'var(--text3)', fontSize: 13, margin: 0 },
  warn: { color: 'var(--red)', fontSize: 13 },
  badge: { background: 'var(--accent-bg)', color: 'var(--accent)', borderRadius: 6, padding: '6px 10px', fontSize: 12, whiteSpace: 'nowrap' },
  panel: { background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8, padding: 16, marginBottom: 16, overflow: 'hidden' },
  metrics: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 14 },
  metricLabel: { color: 'var(--text3)', fontSize: 11, marginBottom: 3 },
  metricValue: { fontSize: 17 },
  infoValue: { color: 'var(--text2)', fontSize: 13 },
  empty: { color: 'var(--text3)', padding: 30, textAlign: 'center' },
  twoCol: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 },
  ruleGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 },
  gaps: { border: '1px solid rgba(217,119,6,0.35)', background: 'rgba(217,119,6,0.08)', color: 'var(--gold)', borderRadius: 8, padding: 14, marginBottom: 16 },
}
