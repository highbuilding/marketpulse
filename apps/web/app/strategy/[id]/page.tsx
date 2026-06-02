'use client'
import Link from 'next/link'
import {
  DEMO_STRATEGIES, DEMO_EQUITY_CURVE, DEMO_METRICS, DEMO_TRADES,
} from '@/lib/demo_fixtures'

function EquityCurve({ points }: { points: number[] }) {
  const W = 720, H = 220, pad = 8
  const min = Math.min(...points), max = Math.max(...points)
  const span = max - min || 1
  const path = points.map((p, i) => {
    const x = pad + (i / (points.length - 1)) * (W - pad * 2)
    const y = pad + (1 - (p - min) / span) * (H - pad * 2)
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 220 }}>
      <path d={`${path} L${W - pad},${H - pad} L${pad},${H - pad} Z`} fill="var(--accent-bg)" opacity={0.5} />
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} />
    </svg>
  )
}

export default function StrategyReport({ params }: { params: { id: string } }) {
  const { id } = params
  const strategy = DEMO_STRATEGIES.find((s) => s.id === id)

  return (
    <div style={{ padding: 24, maxWidth: 860, margin: '0 auto' }}>
      <div style={{ background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 12, padding: '7px 12px', borderRadius: 6, marginBottom: 16 }}>
        🧪 功能预览 · 回测结果为演示用途
      </div>
      <Link href="/strategy" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← 返回策略列表</Link>
      <h1 style={{ fontSize: 20, margin: '8px 0 4px' }}>{strategy?.name ?? id}</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginBottom: 18 }}>{strategy?.desc ?? '回测报告'}</p>

      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header">资金曲线</div>
        <div className="panel-body"><EquityCurve points={DEMO_EQUITY_CURVE} /></div>
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header">绩效指标</div>
        <div className="panel-body">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
            {DEMO_METRICS.map((m) => (
              <div key={m.label}>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{m.label}</div>
                <div className="font-mono" style={{ fontSize: 18 }}>{m.value}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">交易记录</div>
        <div className="panel-body">
          <table className="data-table">
            <thead><tr><th>日期</th><th>方向</th><th>标的</th><th>价格</th><th>盈亏</th></tr></thead>
            <tbody>
              {DEMO_TRADES.map((t, i) => (
                <tr key={i} style={{ cursor: 'default' }}>
                  <td style={{ textAlign: 'left' }} className="font-mono">{t.date}</td>
                  <td style={{ textAlign: 'left', color: t.dir === 'buy' ? 'var(--red)' : 'var(--green)' }}>{t.dir === 'buy' ? '买入' : '卖出'}</td>
                  <td style={{ textAlign: 'left' }} className="font-mono">{t.symbol}</td>
                  <td className="font-mono">{t.price}</td>
                  <td className="font-mono">{t.pnl}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
