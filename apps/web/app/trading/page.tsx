'use client'
import { useState } from 'react'
import {
  DEMO_POSITIONS, DEMO_ORDERS, DEMO_RISK_TOGGLES, type DemoRiskToggle,
} from '@/lib/demo_fixtures'

export default function TradingPage() {
  const [toggles, setToggles] = useState<DemoRiskToggle[]>(DEMO_RISK_TOGGLES)
  const flip = (i: number) =>
    setToggles((ts) => ts.map((t, idx) => (idx === i ? { ...t, on: !t.on } : t)))

  return (
    <div style={{ padding: 24, maxWidth: 980, margin: '0 auto' }}>
      <div style={{ background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 12, padding: '7px 12px', borderRadius: 6, marginBottom: 16 }}>
        🧪 功能预览 · 持仓、订单与风控均为演示用途
      </div>
      <h1 style={{ fontSize: 20, marginBottom: 12 }}>⚡ 自动交易</h1>

      {/* 状态条 */}
      <div style={{ background: 'rgba(239,68,68,0.1)', color: 'var(--red)', fontSize: 13, padding: '10px 14px', borderRadius: 8, marginBottom: 18, fontWeight: 600 }}>
        自动交易:已停用(演示)
      </div>

      {/* 持仓 */}
      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header">当前持仓</div>
        <div className="panel-body">
          <table className="data-table">
            <thead><tr><th>标的</th><th>数量</th><th>成本</th><th>现价</th><th>盈亏</th></tr></thead>
            <tbody>
              {DEMO_POSITIONS.map((p) => (
                <tr key={p.symbol} style={{ cursor: 'default' }}>
                  <td style={{ textAlign: 'left' }}><b>{p.name}</b> <span className="font-mono" style={{ color: 'var(--text3)', fontSize: 12 }}>{p.symbol}</span></td>
                  <td className="font-mono">{p.qty}</td>
                  <td className="font-mono">{p.cost}</td>
                  <td className="font-mono">{p.last}</td>
                  <td className={`font-mono ${p.up ? 'text-up' : 'text-down'}`}>{p.pnl}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 订单流 */}
      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header">订单流</div>
        <div className="panel-body">
          <table className="data-table">
            <thead><tr><th>时间</th><th>方向</th><th>标的</th><th>价格</th><th>状态</th></tr></thead>
            <tbody>
              {DEMO_ORDERS.map((o, i) => (
                <tr key={i} style={{ cursor: 'default' }}>
                  <td style={{ textAlign: 'left' }} className="font-mono">{o.ts}</td>
                  <td style={{ textAlign: 'left', color: o.dir === 'buy' ? 'var(--red)' : 'var(--green)' }}>{o.dir === 'buy' ? '买入' : '卖出'}</td>
                  <td style={{ textAlign: 'left' }} className="font-mono">{o.symbol}</td>
                  <td className="font-mono">{o.price}</td>
                  <td style={{ color: 'var(--text2)' }}>{o.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 风控开关 */}
      <div className="panel">
        <div className="panel-header">风控</div>
        <div className="panel-body">
          {toggles.map((t, i) => (
            <div key={t.label} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0', borderBottom: '1px solid rgba(128,128,128,0.08)' }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14 }}>{t.label}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t.desc}</div>
              </div>
              <div onClick={() => flip(i)}
                style={{
                  width: 40, height: 22, borderRadius: 11, cursor: 'pointer', position: 'relative',
                  background: t.on ? 'var(--accent)' : 'var(--bg3)', transition: 'background 0.15s',
                }}>
                <div style={{
                  width: 18, height: 18, borderRadius: '50%', background: '#fff', position: 'absolute', top: 2,
                  left: t.on ? 20 : 2, transition: 'left 0.15s',
                }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
