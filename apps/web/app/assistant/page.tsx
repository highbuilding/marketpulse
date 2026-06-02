'use client'
import { useState } from 'react'
import { DEMO_CHAT, type DemoChatMsg } from '@/lib/demo_fixtures'

export default function AssistantPage() {
  const [msgs, setMsgs] = useState<DemoChatMsg[]>(DEMO_CHAT)
  const [input, setInput] = useState('')

  const send = () => {
    const t = input.trim()
    if (!t) return
    setMsgs((m) => [
      ...m,
      { role: 'user', text: t, ts: '现在' },
      { role: 'ai', text: '演示模式暂不可用:AI 助手为占位预览,接入后将根据实时行情回答你的提问并主动播报。', ts: '现在' },
    ])
    setInput('')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 0px)', maxWidth: 760, margin: '0 auto', padding: '24px 24px 0' }}>
      <div style={{ background: 'var(--accent-bg)', color: 'var(--accent)', fontSize: 12, padding: '7px 12px', borderRadius: 6, marginBottom: 12 }}>
        🧪 功能预览 · 对话为演示用途
      </div>
      <h1 style={{ fontSize: 20, marginBottom: 12 }}>🤖 AI 助手</h1>

      {/* 对话流 */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 12 }}>
        {msgs.map((m, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
            <div style={{
              maxWidth: '78%', padding: '10px 14px', borderRadius: 12, fontSize: 14, lineHeight: 1.6,
              background: m.role === 'user' ? 'var(--accent)' : 'var(--bg2)',
              color: m.role === 'user' ? '#fff' : 'var(--text)',
              border: m.role === 'user' ? 'none' : '1px solid var(--border)',
            }}>
              {m.role === 'ai' && <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>AI 助手 · {m.ts}</div>}
              {m.text}
            </div>
          </div>
        ))}
      </div>

      {/* 输入框 */}
      <div style={{ display: 'flex', gap: 8, padding: '12px 0 20px', borderTop: '1px solid var(--border)' }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="问问今天的行情…"
          onKeyDown={(e) => e.key === 'Enter' && send()}
          style={{ flex: 1, background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', fontSize: 14 }} />
        <button onClick={send}
          style={{ background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, padding: '0 20px', fontSize: 14, cursor: 'pointer' }}>发送</button>
      </div>
    </div>
  )
}
