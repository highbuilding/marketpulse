'use client'

import { useMemo, useState } from 'react'
import useSWR, { mutate } from 'swr'

import { SymbolSearch } from '@/components/SymbolSearch'
import { WatchlistSignalsPanel } from '@/components/WatchlistSignalsPanel'
import {
  addWatchlistSymbol, listWatchlists, listWatchlistSymbols, removeWatchlistSymbol,
} from '@/lib/watchlist_api'
import { fetchSymbolProfile, fetchSymbolQuote } from '@/lib/symbol_api'
import { inferMarket, type Market } from '@/lib/markets'

function fmtVolume(v: number | null | undefined): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(0)
}

function SymbolRow({
  symbol,
  onRemove,
}: {
  symbol: string
  onRemove: (s: string) => void
}) {
  const { data: profile } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))
  const { data: quote } = useSWR(
    `quote:${symbol}`, () => fetchSymbolQuote(symbol),
    { refreshInterval: 15_000 },
  )

  const price = quote?.price
  const pct = quote?.change_pct
  const vol = quote?.volume
  const pctColor =
    pct == null ? 'text-neutral-500'
    : pct > 0 ? 'text-red-400'
    : pct < 0 ? 'text-green-400'
    : 'text-neutral-300'

  return (
    <li className="grid grid-cols-[1fr_100px_90px_110px_40px] gap-2 items-center py-2 border-b border-neutral-800">
      <a
        href={`/symbol/${encodeURIComponent(symbol)}`}
        className="flex items-baseline gap-2 hover:text-blue-400 truncate"
      >
        <span className="font-mono text-xs">{symbol}</span>
        <span className="text-sm text-neutral-300">{profile?.name ?? '—'}</span>
      </a>
      <span className="text-right tabular-nums text-sm">
        {price != null ? price.toFixed(2) : '—'}
      </span>
      <span className={`text-right tabular-nums text-sm ${pctColor}`}>
        {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
      </span>
      <span className="text-right tabular-nums text-sm text-neutral-400">
        {fmtVolume(vol)}
      </span>
      <button
        onClick={() => onRemove(symbol)}
        className="text-xs text-red-400 hover:text-red-300 justify-self-end"
      >
        移除
      </button>
    </li>
  )
}

const MARKET_TABS: { key: Market; label: string; placeholder: string }[] = [
  { key: 'ashare', label: 'A 股',    placeholder: '搜索代码或名称(如 600519 / 茅台)' },
  { key: 'hk',     label: '港股',    placeholder: '搜索港股(如 9988 / 腾讯)' },
  { key: 'us',     label: '美股',    placeholder: '搜索美股(如 AAPL / Apple)' },
  { key: 'crypto', label: '加密货币', placeholder: '搜索加密货币(如 BTC/USDT)' },
]

export default function WatchlistPage() {
  const { data: lists } = useSWR('wls', listWatchlists)
  const [activeId, setActiveId] = useState<number | null>(null)
  const currentId = activeId ?? lists?.watchlists[0]?.id ?? null

  const [marketTab, setMarketTab] = useState<Market>('ashare')

  const { data: items } = useSWR(
    currentId ? `wl:${currentId}` : null,
    () => listWatchlistSymbols(currentId!),
  )

  const symbolsForTab = useMemo(
    () => (items?.symbols ?? []).filter((s) => inferMarket(s) === marketTab),
    [items, marketTab],
  )

  const tabMeta = MARKET_TABS.find((t) => t.key === marketTab)!
  const isSkeletonTab = marketTab === 'hk' || marketTab === 'crypto'

  async function onAdd(hitSymbol: string) {
    if (!currentId) return
    await addWatchlistSymbol(currentId, hitSymbol)
    mutate(`wl:${currentId}`)
    setTimeout(() => {
      mutate((key) => typeof key === 'string' && key.startsWith('wl:events:'))
    }, 6_000)
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

      {/* 市场 tab */}
      <div className="flex gap-1 border-b border-neutral-800">
        {MARKET_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setMarketTab(t.key)}
            className={`px-3 py-1.5 text-sm border-b-2 transition-colors ${
              marketTab === t.key
                ? 'text-white border-blue-500'
                : 'text-neutral-400 border-transparent hover:text-neutral-200'
            }`}
          >
            {t.label}
            {isSkeletonTab && t.key === marketTab && (
              <span className="ml-2 text-xs text-neutral-500">(骨架)</span>
            )}
          </button>
        ))}
      </div>

      <SymbolSearch
        key={marketTab}
        market={marketTab}
        placeholder={tabMeta.placeholder}
        onSelect={(hit) => onAdd(hit.symbol)}
      />

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <div className="grid grid-cols-[1fr_100px_90px_110px_40px] gap-2 text-xs text-neutral-500 pb-2 border-b border-neutral-800">
          <span>标的</span>
          <span className="text-right">价格</span>
          <span className="text-right">涨跌幅</span>
          <span className="text-right">成交量</span>
          <span />
        </div>
        {isSkeletonTab && symbolsForTab.length === 0 && (
          <p className="text-sm text-neutral-500 mt-3">
            {marketTab === 'hk' ? '港股' : '加密货币'} 行情/信号本期暂未接入,可先添加标的占位。
          </p>
        )}
        {!isSkeletonTab && symbolsForTab.length === 0 && (
          <p className="text-sm text-neutral-500 mt-3">空</p>
        )}
        <ul>
          {symbolsForTab.map((s) => (
            <SymbolRow key={s} symbol={s} onRemove={onRemove} />
          ))}
        </ul>
      </section>

      <WatchlistSignalsPanel
        symbols={symbolsForTab}
        market={marketTab}
      />
    </main>
  )
}
