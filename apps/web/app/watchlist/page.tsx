'use client'

import { useState } from 'react'
import useSWR, { mutate } from 'swr'

import {
  addWatchlistSymbol, listWatchlists, listWatchlistSymbols, removeWatchlistSymbol,
} from '@/lib/watchlist_api'

export default function WatchlistPage() {
  const { data: lists } = useSWR('wls', listWatchlists)
  const [activeId, setActiveId] = useState<number | null>(null)
  const currentId = activeId ?? lists?.watchlists[0]?.id ?? null

  const { data: items } = useSWR(
    currentId ? `wl:${currentId}` : null,
    () => listWatchlistSymbols(currentId!),
  )

  const [newSym, setNewSym] = useState('')

  async function onAdd() {
    if (!currentId || !newSym.trim()) return
    await addWatchlistSymbol(currentId, newSym.trim().toUpperCase())
    setNewSym('')
    mutate(`wl:${currentId}`)
  }

  async function onRemove(sym: string) {
    if (!currentId) return
    await removeWatchlistSymbol(currentId, sym)
    mutate(`wl:${currentId}`)
  }

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">我的关注</h1>
        <a href="/dashboard" className="text-xs text-neutral-400 hover:text-neutral-200">← Dashboard</a>
      </header>

      <div className="flex gap-2">
        {lists?.watchlists.map((w) => (
          <button
            key={w.id}
            onClick={() => setActiveId(w.id)}
            className={`px-3 py-1 text-sm rounded ${w.id === currentId ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400'}`}
          >
            {w.name}
          </button>
        ))}
      </div>

      <div className="flex gap-2 items-center">
        <input
          value={newSym}
          onChange={(e) => setNewSym(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') onAdd() }}
          placeholder="600519.SH"
          className="bg-neutral-900 border border-neutral-700 text-sm rounded px-2 py-1 font-mono text-white"
        />
        <button onClick={onAdd} className="bg-blue-600 text-white text-sm rounded px-3 py-1">
          加入
        </button>
      </div>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        {items && items.symbols.length === 0 && <p className="text-sm text-neutral-500">空</p>}
        <ul className="space-y-1">
          {items?.symbols.map((s) => (
            <li key={s} className="flex justify-between items-center py-1 border-b border-neutral-800">
              <a href={`/symbol/${encodeURIComponent(s)}`} className="font-mono hover:text-blue-400">{s}</a>
              <button onClick={() => onRemove(s)} className="text-xs text-red-400 hover:text-red-300">移除</button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
