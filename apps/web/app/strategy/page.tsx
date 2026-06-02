'use client'
import Link from 'next/link'
import { DEMO_STRATEGIES, type DemoStrategy } from '@/lib/demo_fixtures'

const STATUS_LABEL: Record<DemoStrategy['status'], { text: string; color: string }> = {
  running: { text: '运行中', color: 'var(--green)' },
  paused:  { text: '已暂停', color: 'var(--text3)' },
  draft:   { text: '草稿',   color: 'var(--accent)' },
}

export default function StrategyPage() {
  return (
    <div style={{ padding: 24, maxWidth: 980, margin: '0 auto' }}>
      <div style={{ background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 12, padding: '7px 12px', borderRadius: 6, marginBottom: 16 }}>
        🧪 功能预览 · 数据为演示用途,策略与回测结果均为示例
      </div>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>🧪 策略回测</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginBottom: 18 }}>选择策略查看回测报告。点击卡片进入详情。</p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
        {DEMO_STRATEGIES.map((s) => {
          const st = STATUS_LABEL[s.status]
          return (
            <Link key={s.id} href={`/strategy/${s.id}`} className="panel"
              style={{ padding: 16, textDecoration: 'none', color: 'inherit', display: 'block' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <b style={{ fontSize: 15 }}>{s.name}</b>
                <span style={{ fontSize: 11, color: st.color }}>● {st.text}</span>
              </div>
              <p style={{ fontSize: 12, color: 'var(--text3)', minHeight: 32, marginBottom: 12 }}>{s.desc}</p>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>年化</div>
                  <div className="font-mono text-up" style={{ fontSize: 16 }}>+{s.annualReturn}%</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>最大回撤</div>
                  <div className="font-mono text-down" style={{ fontSize: 16 }}>{s.maxDrawdown}%</div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
