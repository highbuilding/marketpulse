'use client'
import { useState } from 'react'

export default function LoginPage() {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr('')
    const r = await fetch('/api/auth/login', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode: pw }),
    })
    if (r.ok) window.location.href = '/'
    else setErr('口令错误')
  }
  return (
    <main className="min-h-screen flex items-center justify-center bg-neutral-950">
      <form onSubmit={submit} className="space-y-3 p-6 rounded-lg border border-neutral-800 bg-neutral-900">
        <div className="text-neutral-200 text-sm">MarketPulse · 输入口令</div>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)}
          className="w-64 px-3 py-2 rounded bg-neutral-800 text-neutral-100 text-sm" placeholder="口令" />
        {err && <div className="text-red-400 text-xs">{err}</div>}
        <button className="w-full px-3 py-2 rounded bg-neutral-700 text-white text-sm">进入</button>
      </form>
    </main>
  )
}
