'use client'

import { useState } from 'react'
import useSWR from 'swr'

import {
  deleteSymbolConfig, listSymbolConfigs, upsertSymbolConfig,
  type SignalInterval, type SymbolConfig,
} from '@/lib/notifications_api'
import { listWatchlists, listWatchlistSymbols } from '@/lib/watchlist_api'

const ALL_INTERVALS: SignalInterval[] = ['15m', '30m', '60m', '4h', '1d']
const INTERVAL_LABEL: Record<SignalInterval, string> = {
  '15m': '15m', '30m': '30m', '60m': '1h', '4h': '4h', '1d': '1d',
}

export function SymbolConfigManager() {
  const [editing, setEditing] = useState<SymbolConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const { data, mutate, isLoading, error } = useSWR(
    '/api/notifications/symbol-config',
    () => listSymbolConfigs(),
  )
  const configs = data?.configs ?? []

  async function onImportFromWatchlist() {
    setBusy(true); setMsg(null)
    try {
      const { watchlists } = await listWatchlists()
      const seen = new Set<string>()
      for (const wl of watchlists) {
        if (wl.is_archived) continue
        const { symbols } = await listWatchlistSymbols(wl.id)
        for (const s of symbols) seen.add(s)
      }
      const existing = new Set(configs.map(c => c.symbol))
      const toAdd = [...seen].filter(s => !existing.has(s))
      for (const sym of toAdd) {
        await upsertSymbolConfig(sym, ['1d'])
      }
      await mutate()
      setMsg(`已导入 ${toAdd.length} 个 symbol(默认 1d)`)
    } catch (e) {
      setMsg(`导入失败:${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  async function onSave() {
    if (!editing) return
    setBusy(true); setMsg(null)
    try {
      // 1d 强制注入(后端也会注入,前端保险)
      const intervals = Array.from(new Set<SignalInterval>(['1d', ...editing.intervals]))
      await upsertSymbolConfig(editing.symbol, intervals)
      setEditing(null)
      await mutate()
    } catch (e) {
      setMsg(`保存失败:${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  async function onDelete(symbol: string) {
    if (!confirm(`删除 ${symbol} 的通知配置?`)) return
    await deleteSymbolConfig(symbol)
    await mutate()
  }

  function toggleInterval(iv: SignalInterval) {
    if (!editing) return
    if (iv === '1d') return  // 1d 锁死
    const has = editing.intervals.includes(iv)
    setEditing({
      ...editing,
      intervals: has ? editing.intervals.filter(x => x !== iv) : [...editing.intervals, iv],
    })
  }

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4 space-y-3">
      <header className="flex items-center justify-between">
        <h2 className="text-base font-semibold">Symbol 通知配置</h2>
        <button onClick={onImportFromWatchlist} disabled={busy}
                className="text-xs px-3 py-1 rounded bg-blue-700 hover:bg-blue-600 disabled:opacity-40">
          从关注列表批量导入
        </button>
      </header>

      <p className="text-xs text-neutral-500">
        每个 symbol 可单独勾选周期。<span className="text-neutral-400">1d 必勾,不可关闭</span>;
        其他默认不勾,勾选后该周期出现 CD 信号才会发邮件。
      </p>

      {msg && <p className="text-xs text-yellow-400">{msg}</p>}
      {error && <p className="text-xs text-red-400">加载失败:{String(error)}</p>}

      {isLoading ? <p className="text-sm text-neutral-500">加载中…</p>
        : configs.length === 0 ? <p className="text-sm text-neutral-500">暂无配置。点上方"批量导入"添加。</p>
        : (
          <table className="w-full text-sm">
            <thead className="text-xs text-neutral-500">
              <tr>
                <th className="text-left py-1">Symbol</th>
                <th className="text-left">已启用周期</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {configs.map(c => (
                <tr key={c.symbol} className="border-t border-neutral-800">
                  <td className="py-1 font-mono">{c.symbol}</td>
                  <td>
                    <div className="flex gap-1 flex-wrap">
                      {ALL_INTERVALS.filter(iv => c.intervals.includes(iv)).map(iv => (
                        <span key={iv} className={`px-2 py-0.5 text-xs rounded ${iv === '1d' ? 'bg-purple-800' : 'bg-neutral-700'}`}>
                          {INTERVAL_LABEL[iv]}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="text-right">
                    <button onClick={() => setEditing({ symbol: c.symbol, intervals: [...c.intervals] })}
                            className="text-xs text-blue-400 hover:text-blue-300 mr-2">编辑</button>
                    <button onClick={() => onDelete(c.symbol)}
                            className="text-xs text-red-400 hover:text-red-300">删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      {editing && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setEditing(null)}>
          <div className="bg-neutral-900 border border-neutral-700 rounded-lg p-5 w-96" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold mb-3">配置 <span className="font-mono">{editing.symbol}</span></h3>
            <div className="space-y-2">
              {ALL_INTERVALS.map(iv => {
                const checked = editing.intervals.includes(iv)
                const isDay = iv === '1d'
                return (
                  <label key={iv} className={`flex items-center gap-2 text-sm ${isDay ? 'text-neutral-500' : 'cursor-pointer'}`}>
                    <input type="checkbox" checked={checked || isDay} disabled={isDay}
                           onChange={() => toggleInterval(iv)} />
                    {INTERVAL_LABEL[iv]}{isDay && <span className="text-xs">(必勾)</span>}
                  </label>
                )
              })}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setEditing(null)} className="text-xs px-3 py-1 rounded bg-neutral-800 hover:bg-neutral-700">取消</button>
              <button onClick={onSave} disabled={busy} className="text-xs px-3 py-1 rounded bg-green-700 hover:bg-green-600 disabled:opacity-40">保存</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
