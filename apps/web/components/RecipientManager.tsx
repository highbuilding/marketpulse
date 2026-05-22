'use client'

import { useState } from 'react'
import useSWR from 'swr'

import {
  addRecipient, deleteRecipient, listRecipients, sendTestEmail,
  setRecipientEnabled,
  type Market, type Recipient,
} from '@/lib/notifications_api'

const MARKETS: { key: Market; label: string }[] = [
  { key: 'ashare', label: 'A 股' },
  { key: 'us', label: '美股' },
  { key: 'hk', label: '港股' },
  { key: 'crypto', label: 'Crypto' },
]

export function RecipientManager() {
  const [market, setMarket] = useState<Market>('ashare')
  const [address, setAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const { data, mutate, isLoading, error } = useSWR(
    `/api/notifications/recipients?market=${market}`,
    () => listRecipients(market),
  )
  const recipients: Recipient[] = data?.recipients ?? []

  async function onAdd() {
    if (!address.trim()) return
    setBusy(true); setMsg(null)
    try {
      await addRecipient(market, 'email', address.trim())
      setAddress('')
      await mutate()
      setMsg('已添加')
    } catch (e) {
      setMsg(`添加失败:${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  async function onToggle(r: Recipient) {
    await setRecipientEnabled(r.id, !r.enabled)
    await mutate()
  }

  async function onDelete(r: Recipient) {
    if (!confirm(`删除收件人 ${r.address}?`)) return
    await deleteRecipient(r.id)
    await mutate()
  }

  async function onTest() {
    setBusy(true); setMsg(null)
    try {
      const res = await sendTestEmail(market)
      if (res.ok) setMsg(`已发送测试邮件到 ${res.sent_to} 个收件人`)
      else setMsg(`测试失败:${res.error ?? '未知错误'}`)
    } catch (e) {
      setMsg(`请求失败:${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4 space-y-3">
      <header className="flex items-center justify-between">
        <h2 className="text-base font-semibold">收件人管理</h2>
        <button onClick={onTest} disabled={busy || recipients.filter(r => r.enabled).length === 0}
                className="text-xs px-3 py-1 rounded bg-blue-700 hover:bg-blue-600 disabled:opacity-40">
          发送测试邮件
        </button>
      </header>

      <div className="flex gap-1">
        {MARKETS.map(m => (
          <button key={m.key} onClick={() => setMarket(m.key)}
                  className={`px-3 py-1 text-xs rounded ${market === m.key ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'}`}>
            {m.label}
          </button>
        ))}
      </div>

      <div className="flex gap-2">
        <input value={address} onChange={(e) => setAddress(e.target.value)}
               placeholder="email 地址" type="email"
               onKeyDown={(e) => e.key === 'Enter' && onAdd()}
               className="flex-1 px-2 py-1 text-sm bg-neutral-900 border border-neutral-800 rounded text-white" />
        <select disabled value="email" className="px-2 py-1 text-sm bg-neutral-900 border border-neutral-800 rounded text-neutral-400">
          <option value="email">Email</option>
          <option value="wechat" disabled>微信(规划中)</option>
        </select>
        <button onClick={onAdd} disabled={busy || !address.trim()}
                className="px-3 py-1 text-sm rounded bg-green-700 hover:bg-green-600 disabled:opacity-40">
          添加
        </button>
      </div>

      {msg && <p className="text-xs text-yellow-400">{msg}</p>}
      {error && <p className="text-xs text-red-400">加载失败:{String(error)}</p>}

      {isLoading ? <p className="text-sm text-neutral-500">加载中…</p>
        : recipients.length === 0 ? <p className="text-sm text-neutral-500">暂无收件人</p>
        : (
          <table className="w-full text-sm">
            <thead className="text-xs text-neutral-500">
              <tr><th className="text-left py-1">地址</th><th>通道</th><th>启用</th><th>操作</th></tr>
            </thead>
            <tbody>
              {recipients.map(r => (
                <tr key={r.id} className="border-t border-neutral-800">
                  <td className="py-1 font-mono">{r.address}</td>
                  <td className="text-center text-xs text-neutral-400">{r.channel}</td>
                  <td className="text-center">
                    <input type="checkbox" checked={r.enabled} onChange={() => onToggle(r)} />
                  </td>
                  <td className="text-center">
                    <button onClick={() => onDelete(r)} className="text-xs text-red-400 hover:text-red-300">删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </section>
  )
}
