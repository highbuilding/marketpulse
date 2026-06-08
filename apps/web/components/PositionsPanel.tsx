'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'

import { useMarket } from '@/lib/market-context'
import type { Position } from '@/lib/types'
import { listPositions } from '@/lib/positions_api'

/**
 * 概览页持仓展示面板(只读)。
 * 显示活跃持仓 + 实时现价(bars/history 与自选同源) + 浮动盈亏(现价 vs 开仓价)。
 * 增删管理在独立 /positions 页; 本组件不做 CRUD。
 */
export function PositionsPanel() {
  const { market } = useMarket()
  const [positions, setPositions] = useState<Position[]>([])
  const [prices, setPrices] = useState<Record<string, number>>({})

  const isAshare = market === 'ashare'

  // 拉活跃持仓
  useEffect(() => {
    if (!isAshare) { setPositions([]); return }
    let alive = true
    listPositions(market, false)
      .then((d) => { if (alive) setPositions(d.positions) })
      .catch(() => { if (alive) setPositions([]) })
    return () => { alive = false }
  }, [market, isAshare])

  // 持仓标的现价: bars/history 5m 末根 close(与自选同源), 60s 轮询
  const symbols = useMemo(() => positions.map((p) => p.symbol), [positions])
  useEffect(() => {
    if (symbols.length === 0) { setPrices({}); return }
    let alive = true
    const load = () => {
      Promise.all(symbols.map((s) =>
        fetch(`/api/symbols/${encodeURIComponent(s)}/bars/history?interval=5m&limit=1`)
          .then((r) => r.json())
          .then((d) => {
            const bars = (d.bars ?? []) as { close: number }[]
            return [s, bars.length ? bars[bars.length - 1].close : NaN] as const
          })
          .catch(() => [s, NaN] as const),
      )).then((pairs) => {
        if (!alive) return
        const m: Record<string, number> = {}
        for (const [s, px] of pairs) if (Number.isFinite(px)) m[s] = px
        setPrices(m)
      })
    }
    load()
    const id = setInterval(load, 60_000)
    return () => { alive = false; clearInterval(id) }
  }, [symbols])

  if (!isAshare) {
    return (
      <div className="panel">
        <div className="panel-header">📋 持仓</div>
        <div style={{ padding: 24, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
          手动持仓当前仅支持 A 股
        </div>
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="panel-header">
        📋 持仓
        <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{positions.length} 只</span>
        <Link href="/positions" style={{ fontSize: 12, color: 'var(--accent)', marginLeft: 'auto' }}>管理 →</Link>
      </div>
      <table className="data-table">
        <thead><tr><th>名称</th><th>现价</th><th>开仓价</th><th>股数</th><th>盈亏</th></tr></thead>
        <tbody>
          {positions.length === 0 && (
            <tr><td colSpan={5} style={{ textAlign: 'center', padding: 24, color: 'var(--text3)' }}>
              暂无持仓,去<Link href="/positions" style={{ color: 'var(--accent)' }}>管理页</Link>添加
            </td></tr>
          )}
          {positions.map((p) => {
            const px = prices[p.symbol]
            const hasPx = Number.isFinite(px)
            const cost = p.cost_price
            let pnl: number | null = null
            let pnlPct: number | null = null
            if (hasPx && cost != null && cost > 0) {
              pnl = (px - cost) * p.quantity
              pnlPct = (px - cost) / cost * 100
            }
            const up = (pnl ?? 0) >= 0
            const pnlColor = pnl == null ? 'var(--text3)' : up ? 'var(--red)' : 'var(--green)'
            return (
              <tr key={p.id}>
                <td>
                  <Link href={`/symbol/${encodeURIComponent(p.symbol)}`} style={{ textDecoration: 'none', color: 'inherit' }}>
                    <span style={{ fontWeight: 500, display: 'block' }}>{p.name || p.symbol}</span>
                    <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>{p.symbol}</span>
                  </Link>
                </td>
                <td style={{ fontFamily: 'monospace' }}>{hasPx ? px.toFixed(2) : '—'}</td>
                <td style={{ fontFamily: 'monospace', color: 'var(--text2)' }}>{cost != null ? cost.toFixed(2) : '—'}</td>
                <td style={{ fontFamily: 'monospace', color: 'var(--text2)' }}>{p.quantity}</td>
                <td style={{ fontFamily: 'monospace', color: pnlColor }}>
                  {pnl != null
                    ? <span>{up ? '+' : ''}{pnl.toFixed(0)}<span style={{ fontSize: 11, marginLeft: 4 }}>({up ? '+' : ''}{pnlPct!.toFixed(2)}%)</span></span>
                    : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
