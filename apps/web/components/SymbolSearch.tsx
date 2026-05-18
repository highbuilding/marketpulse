'use client'

import { useEffect, useRef, useState } from 'react'

import { searchSymbols } from '@/lib/symbol_api'
import type { SearchHit } from '@/lib/types'

interface Props {
  placeholder?: string
  market?: string  // 限定搜索 scope, 例 'us' / 'ashare';未传则全市场搜
  onSelect: (hit: SearchHit) => void
}

export function SymbolSearch({ placeholder = '搜索代码或名称…', market, onSelect }: Props) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!q.trim()) {
      setHits([])
      return
    }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const resp = await searchSymbols(q.trim(), 15, market)
        setHits(resp.hits)
        setOpen(true)
      } catch {
        setHits([])
      } finally {
        setLoading(false)
      }
    }, 250)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [q, market])

  // 点击外部关闭
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div ref={wrapperRef} className="relative">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => hits.length > 0 && setOpen(true)}
        placeholder={placeholder}
        className="bg-neutral-900 border border-neutral-700 text-sm rounded px-3 py-1.5 w-72 text-white"
      />
      {open && (hits.length > 0 || loading) && (
        <div className="absolute z-10 mt-1 w-72 bg-neutral-900 border border-neutral-700 rounded shadow-lg max-h-72 overflow-y-auto">
          {loading && <div className="px-3 py-2 text-xs text-neutral-500">搜索中…</div>}
          {hits.map((h) => (
            <button
              key={h.symbol}
              onClick={() => {
                onSelect(h)
                setQ('')
                setHits([])
                setOpen(false)
              }}
              className="w-full text-left px-3 py-2 text-sm hover:bg-neutral-800 flex items-center justify-between"
            >
              <span className="truncate">{h.name}</span>
              <span className="font-mono text-xs text-neutral-500 ml-2">{h.symbol}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
