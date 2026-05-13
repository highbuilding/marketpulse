'use client'

import { useEffect, useState } from 'react'
import useSWR, { mutate } from 'swr'

import { SymbolSearch } from '@/components/SymbolSearch'
import {
  addWatchlistSymbol, listWatchlists, listWatchlistSymbols, removeWatchlistSymbol,
} from '@/lib/watchlist_api'
import { fetchSymbolProfile } from '@/lib/symbol_api'

function SymbolRow({ symbol, onRemove }: { symbol: string; onRemove: (s: string) => void }) {
  const { data } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))
  return (
    <li className="flex justify-between items-center py-2 border-b border-neutral-800">
      <a href={`/symbol/${encodeURIComponent(symbol)}`}
         className="flex items-baseline gap-3 hover:text-blue-400">
        <span className="font-mono text-sm">{symbol}</span>
        <span className="text-sm text-neutral-300">{data?.name ?? '—'}</span>
      </a>
      <button onClick={() => onRemove(symbol)} className="text-xs text-red-400 hover:text-red-300">移除</button>
    </li>
  )
}

export default function WatchlistPage() {
  const { data: lists } = useSWR('wls', listWatchlists)
  const [activeId, setActiveId] = useState<number | null>(null)
  const currentId = activeId ?? lists?.watchlists[0]?.id ?? null

  const { data: items } = useSWR(
    currentId ? `wl:${currentId}` : null,
    () => listWatchlistSymbols(currentId!),
  )

  async function onAdd(hitSymbol: string) {
    if (!currentId) return
    await addWatchlistSymbol(currentId, hitSymbol)
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
        <a href="/market" className="text-xs text-neutral-400 hover:text-neutral-200">← 市场</a>
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

      <SymbolSearch
        placeholder="搜索代码或名称(如 600519 / 茅台)"
        onSelect={(hit) => onAdd(hit.symbol)}
      />

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        {items && items.symbols.length === 0 && <p className="text-sm text-neutral-500">空</p>}
        <ul>
          {items?.symbols.map((s) => <SymbolRow key={s} symbol={s} onRemove={onRemove} />)}
        </ul>
      </section>
    </main>
  )
}
