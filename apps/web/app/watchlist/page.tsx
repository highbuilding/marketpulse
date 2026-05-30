'use client'

import { useMemo, useState } from 'react'
import useSWR, { mutate } from 'swr'
import Link from 'next/link'

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

function SymbolRow({ symbol, onRemove }: { symbol: string; onRemove: (s: string) => void }) {
  const { data: profile } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))
  const { data: quote } = useSWR(`quote:${symbol}`, () => fetchSymbolQuote(symbol), { refreshInterval: 15_000 })

  const price = quote?.price
  const pct = quote?.change_pct
  const vol = quote?.volume
  const pctCls = pct == null ? '' : pct > 0 ? 'text-up' : pct < 0 ? 'text-down' : ''

  return (
    <tr>
      <td>
        <Link href={`/symbol/${encodeURIComponent(symbol)}`} style={{ textDecoration: 'none', color: 'inherit' }}>
          <span style={{ fontWeight: 500, display: 'block' }}>{profile?.name ?? symbol}</span>
          <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>{symbol}</span>
        </Link>
      </td>
      <td style={{ fontFamily: 'monospace' }}>{price != null ? price.toFixed(2) : '—'}</td>
      <td><span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4 }} className={pctCls}>{pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}</span></td>
      <td style={{ fontFamily: 'monospace', color: 'var(--text2)' }}>{fmtVolume(vol)}</td>
      <td><button onClick={() => onRemove(symbol)} style={{ color: 'var(--red)', fontSize: 12, background: 'none', border: 'none', cursor: 'pointer' }}>移除</button></td>
    </tr>
  )
}

const MARKET_TABS: { key: Market; label: string; placeholder: string }[] = [
  { key: 'ashare', label: 'A 股', placeholder: '搜索代码或名称(如 600519 / 茅台)' },
  { key: 'hk', label: '港股', placeholder: '搜索港股(如 9988 / 腾讯)' },
  { key: 'us', label: '美股', placeholder: '搜索美股(如 AAPL / Apple)' },
  { key: 'crypto', label: 'Crypto', placeholder: '搜索加密货币(如 BTC)' },
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
    () => (items?.symbols ?? []).filter((s: string) => inferMarket(s) === marketTab),
    [items, marketTab],
  )

  const tabMeta = MARKET_TABS.find((t) => t.key === marketTab)!

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
    <div style={{ padding: 20 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>⭐ 自选股</h1>
        <p style={{ color: 'var(--text2)' }}>管理跨市场关注列表</p>
      </div>

      {/* Watchlist selector */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
        {lists?.watchlists.map((w: any) => (
          <button
            key={w.id}
            onClick={() => setActiveId(w.id)}
            className={`int-tab ${w.id === currentId ? 'active' : ''}`}
          >{w.name}</button>
        ))}
      </div>

      {/* Market tabs */}
      <div style={{ display: 'flex', gap: 2, marginBottom: 16 }}>
        {MARKET_TABS.map((t) => (
          <button key={t.key} className={`mkt-tab ${marketTab === t.key ? 'active' : ''}`} onClick={() => setMarketTab(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Search */}
      <div style={{ marginBottom: 16 }}>
        <SymbolSearch key={marketTab} market={marketTab} placeholder={tabMeta.placeholder} onSelect={(hit: any) => onAdd(hit.symbol)} />
      </div>

      {/* Symbols table */}
      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-header">标的列表 <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{symbolsForTab.length} 只</span></div>
        <table className="data-table">
          <thead><tr><th>名称</th><th>价格</th><th>涨跌幅</th><th>成交量</th><th></th></tr></thead>
          <tbody>
            {symbolsForTab.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                {marketTab === 'hk' ? '港股本期暂未接入' : '暂无标的，搜索添加'}
              </td></tr>
            )}
            {symbolsForTab.map((s: string) => <SymbolRow key={s} symbol={s} onRemove={onRemove} />)}
          </tbody>
        </table>
      </div>

      <WatchlistSignalsPanel symbols={symbolsForTab} market={marketTab} />
    </div>
  )
}
