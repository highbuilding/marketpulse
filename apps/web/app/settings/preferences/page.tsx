'use client'
import { useState } from 'react'

function Row({ label, desc, children }: { label: string; desc: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', borderBottom: '1px solid rgba(128,128,128,0.08)' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 14 }}>{label}</div>
        <div style={{ fontSize: 12, color: 'var(--text3)' }}>{desc}</div>
      </div>
      {children}
    </div>
  )
}

const selStyle: React.CSSProperties = {
  background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)',
  borderRadius: 5, padding: '5px 10px', fontSize: 13,
}

export default function PreferencesSettings() {
  const [theme, setTheme] = useState('dark')
  const [market, setMarket] = useState('ashare')
  const [colorScheme, setColorScheme] = useState('cn')

  return (
    <div>
      <div style={{ background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 12, padding: '7px 12px', borderRadius: 6, marginBottom: 16 }}>
        🧪 功能预览 · 数据为演示用途
      </div>
      <div className="panel">
        <div className="panel-body">
          <Row label="主题" desc="界面明暗风格">
            <select value={theme} onChange={(e) => setTheme(e.target.value)} style={selStyle}>
              <option value="dark">暗色</option>
              <option value="light">亮色</option>
            </select>
          </Row>
          <Row label="默认市场" desc="打开应用时的默认市场">
            <select value={market} onChange={(e) => setMarket(e.target.value)} style={selStyle}>
              <option value="ashare">A股</option>
              <option value="hk">港股</option>
              <option value="us">美股</option>
              <option value="crypto">Crypto</option>
            </select>
          </Row>
          <Row label="涨跌色口径" desc="红涨绿跌(A股) / 绿涨红跌(欧美)">
            <select value={colorScheme} onChange={(e) => setColorScheme(e.target.value)} style={selStyle}>
              <option value="cn">红涨绿跌</option>
              <option value="us">绿涨红跌</option>
            </select>
          </Row>
        </div>
      </div>
    </div>
  )
}
