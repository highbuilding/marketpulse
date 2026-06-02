'use client'
import { useState } from 'react'
import {
  DEMO_RECIPIENTS, DEMO_SYMBOL_CONFIGS, SIGNAL_INTERVALS,
  type DemoRecipient, type DemoSymbolConfig, type SignalInterval,
} from '@/lib/demo_fixtures'

function DemoBanner() {
  return (
    <div style={{ background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 12, padding: '7px 12px', borderRadius: 6, marginBottom: 16 }}>
      🧪 功能预览 · 数据为演示用途,增删改不会保存
    </div>
  )
}

export default function NotificationSettings() {
  const [recipients, setRecipients] = useState<DemoRecipient[]>(DEMO_RECIPIENTS)
  const [configs, setConfigs] = useState<DemoSymbolConfig[]>(DEMO_SYMBOL_CONFIGS)
  const [newAddr, setNewAddr] = useState('')

  const addRecipient = () => {
    const a = newAddr.trim()
    if (!a) return
    setRecipients((r) => [...r, { id: Date.now(), address: a, enabled: true }])
    setNewAddr('')
  }
  const toggleRecipient = (id: number) =>
    setRecipients((r) => r.map((x) => (x.id === id ? { ...x, enabled: !x.enabled } : x)))
  const delRecipient = (id: number) =>
    setRecipients((r) => r.filter((x) => x.id !== id))

  const toggleInterval = (symbol: string, iv: SignalInterval) =>
    setConfigs((cs) => cs.map((c) => {
      if (c.symbol !== symbol) return c
      if (iv === '1d') return c // 1d 强制保留
      const has = c.intervals.includes(iv)
      return { ...c, intervals: has ? c.intervals.filter((x) => x !== iv) : [...c.intervals, iv] }
    }))

  return (
    <div>
      <DemoBanner />

      {/* 收件人区 */}
      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header">邮件收件人</div>
        <div className="panel-body">
          {recipients.map((r) => (
            <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 0', borderBottom: '1px solid rgba(128,128,128,0.08)' }}>
              <input type="checkbox" checked={r.enabled} onChange={() => toggleRecipient(r.id)} />
              <span className="font-mono" style={{ flex: 1, color: r.enabled ? 'var(--text)' : 'var(--text3)' }}>{r.address}</span>
              <button onClick={() => delRecipient(r.id)}
                style={{ background: 'none', border: '1px solid var(--border)', color: 'var(--text3)', borderRadius: 5, padding: '3px 10px', fontSize: 12, cursor: 'pointer' }}>删除</button>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <input value={newAddr} onChange={(e) => setNewAddr(e.target.value)} placeholder="添加邮箱地址"
              onKeyDown={(e) => e.key === 'Enter' && addRecipient()}
              style={{ flex: 1, background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 5, padding: '6px 10px', fontSize: 13 }} />
            <button onClick={addRecipient}
              style={{ background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 5, padding: '6px 16px', fontSize: 13, cursor: 'pointer' }}>添加</button>
          </div>
        </div>
      </div>

      {/* 按标的周期区 */}
      <div className="panel">
        <div className="panel-header">按标的统计周期</div>
        <div className="panel-body">
          <p style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>勾选每个标的需要扫描并通知的周期(1d 默认启用)。</p>
          {configs.map((c) => (
            <div key={c.symbol} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderBottom: '1px solid rgba(128,128,128,0.08)' }}>
              <b style={{ minWidth: 84 }}>{c.name}</b>
              <span className="font-mono sig-mkt">{c.symbol}</span>
              <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                {SIGNAL_INTERVALS.map((iv) => {
                  const on = c.intervals.includes(iv)
                  const locked = iv === '1d'
                  return (
                    <span key={iv} onClick={() => !locked && toggleInterval(c.symbol, iv)}
                      className={`int-tab ${on ? 'active' : ''}`}
                      style={{ cursor: locked ? 'not-allowed' : 'pointer', opacity: locked ? 0.7 : 1 }}>
                      {iv}
                    </span>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
