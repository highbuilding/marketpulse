'use client'

const SOURCES = [
  { name: 'akshare(A股)',    status: '正常', ok: true },
  { name: 'sina 行情通道',     status: '正常', ok: true },
  { name: 'Alpaca(美股)',    status: '正常', ok: true },
  { name: 'Binance WS(Crypto)', status: '正常', ok: true },
  { name: '港股指数',          status: '待实装', ok: false },
]

export default function AboutSettings() {
  return (
    <div>
      <div style={{ background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 12, padding: '7px 12px', borderRadius: 6, marginBottom: 16 }}>
        🧪 功能预览 · 数据为演示用途
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-header">版本</div>
        <div className="panel-body">
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0' }}>
            <span style={{ color: 'var(--text2)' }}>MarketPulse</span><span className="font-mono">v0.9.0-demo</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0' }}>
            <span style={{ color: 'var(--text2)' }}>构建日期</span><span className="font-mono">2026-06-02</span>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">数据源状态</div>
        <div className="panel-body">
          {SOURCES.map((s) => (
            <div key={s.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(128,128,128,0.08)' }}>
              <span style={{ color: 'var(--text2)' }}>{s.name}</span>
              <span style={{ fontSize: 12, color: s.ok ? 'var(--green)' : 'var(--text3)' }}>
                {s.ok ? '● ' : '○ '}{s.status}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
