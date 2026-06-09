'use client'

import { useMemo } from 'react'
import useSWR, { mutate } from 'swr'
import Link from 'next/link'

import { SymbolSearch } from '@/components/SymbolSearch'
import {
  addWatchlistSymbol, listWatchlists, listWatchlistSymbols, removeWatchlistSymbol,
} from '@/lib/watchlist_api'
import { fetchSymbolProfile } from '@/lib/symbol_api'
import { inferMarket, type Market } from '@/lib/markets'
import { useMarket, MARKET_LABELS } from '@/lib/market-context'
import { useActiveWatchlistId } from '@/lib/use_active_watchlist'

function SymbolRow({ symbol, onRemove }: { symbol: string; onRemove: (s: string) => void }) {
  const { data: profile } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))

  return (
    <tr>
      <td>
        <Link href={`/symbol/${encodeURIComponent(symbol)}`} style={{ textDecoration: 'none', color: 'inherit' }}>
          <span style={{ fontWeight: 500, display: 'block' }}>{profile?.name ?? symbol}</span>
          <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>{symbol}</span>
        </Link>
      </td>
      <td><button onClick={() => onRemove(symbol)} style={{ color: 'var(--red)', fontSize: 12, background: 'none', border: 'none', cursor: 'pointer' }}>移除</button></td>
    </tr>
  )
}

export default function WatchlistPage() {
  const { market } = useMarket()
  const { data: lists } = useSWR('wls', listWatchlists)
  const [activeId, setActiveId] = useActiveWatchlistId()
  const currentId = activeId ?? lists?.watchlists[0]?.id ?? null
  const label = MARKET_LABELS[market]

  const { data: items } = useSWR(
    currentId ? `wl:${currentId}` : null,
    () => listWatchlistSymbols(currentId!),
  )

  const symbolsForTab = useMemo(
    () => (items?.symbols ?? []).filter((s: string) => inferMarket(s) === market),
    [items, market],
  )

  async function onAdd(hitSymbol: string) {
    if (!currentId) return
    try {
      await addWatchlistSymbol(currentId, hitSymbol)
      await mutate(`wl:${currentId}`)
    } catch (e) {
      alert(`添加失败:${e instanceof Error ? e.message : e}`)
    }
  }

  async function onRemove(sym: string) {
    if (!currentId) return
    try {
      await removeWatchlistSymbol(currentId, sym)
      await mutate(`wl:${currentId}`)
    } catch (e) {
      alert(`移除失败:${e instanceof Error ? e.message : e}`)
    }
  }

  return (
    <div style={{ padding: 24, maxWidth: 920, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>⭐ 自选</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginBottom: 16 }}>
        {label.flag} {label.name} · 管理当前市场关注标的(市场跟随顶部切换)
      </p>

      {/* 清单选择(多清单时显示) */}
      {(lists?.watchlists.length ?? 0) > 1 && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
          {lists?.watchlists.map((w: any) => (
            <span key={w.id} onClick={() => setActiveId(w.id)}
              className={`int-tab ${w.id === currentId ? 'active' : ''}`}
              style={{ cursor: 'pointer' }}>{w.name}</span>
          ))}
        </div>
      )}

      {/* 搜索添加(scope 跟随当前市场)。不套 panel: panel 的 overflow:hidden 会裁掉下拉 */}
      <div style={{ position: 'relative', zIndex: 20, marginBottom: 14 }}>
        <SymbolSearch key={market} market={market} coreOnly
          placeholder={`搜索${label.name}代码或名称添加自选`}
          onSelect={(hit: any) => onAdd(hit.symbol)} />
      </div>

      {/* 标的列表 */}
      <div className="panel">
        <div className="panel-header">
          标的列表
          <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{symbolsForTab.length} 只</span>
        </div>
        <table className="data-table">
          <thead><tr><th>名称</th><th></th></tr></thead>
          <tbody>
            {symbolsForTab.length === 0 && (
              <tr><td colSpan={2} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                {market === 'hk' ? '港股本期暂未接入' : '当前市场暂无自选,搜索添加'}
              </td></tr>
            )}
            {symbolsForTab.map((s: string) => <SymbolRow key={s} symbol={s} onRemove={onRemove} />)}
          </tbody>
        </table>
      </div>
    </div>
  )
}
