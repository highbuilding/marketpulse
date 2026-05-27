'use client'

import useSWR from 'swr'
import clsx from 'clsx'

interface RankRow {
  symbol: string
  name: string
  price: number
  change_pct: number
  volume: number
  amount: number
}

interface TopResponse {
  market: 'ashare' | 'hk'
  gainers: RankRow[]
  losers: RankRow[]
}

async function fetchTop(market: 'ashare' | 'hk'): Promise<TopResponse> {
  const r = await fetch(`/api/markets/${market}/top?limit=10`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

function Row({ row }: { row: RankRow }) {
  const up = row.change_pct >= 0
  return (
    <div className="flex items-center justify-between text-sm py-1 border-b border-neutral-800">
      <div className="flex gap-2 min-w-0">
        <span className="font-mono text-neutral-400 shrink-0">{row.symbol}</span>
        <span className="truncate">{row.name}</span>
      </div>
      <span className={clsx('tabular-nums shrink-0 ml-2', up ? 'text-red-400' : 'text-green-400')}>
        {row.price.toFixed(2)} ({up ? '+' : ''}{row.change_pct.toFixed(2)}%)
      </span>
    </div>
  )
}

export function TopMoversCard({ market }: { market: 'ashare' | 'hk' }) {
  const { data, error, isLoading } = useSWR(
    `top:${market}`, () => fetchTop(market), { refreshInterval: 30_000 },
  )

  return (
    <section className="rounded-lg border border-neutral-800 p-4 bg-neutral-950">
      <h2 className="text-lg font-semibold mb-3">
        {market === 'ashare' ? 'A 股 全市场 TOP' : '港股 全市场 TOP'}
      </h2>
      {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {error && <p className="text-sm text-red-400">加载失败</p>}
      {data && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <h3 className="text-xs text-red-400 uppercase mb-1">涨幅榜</h3>
            {data.gainers.map((r) => <Row key={r.symbol} row={r} />)}
          </div>
          <div>
            <h3 className="text-xs text-green-400 uppercase mb-1">跌幅榜</h3>
            {data.losers.map((r) => <Row key={r.symbol} row={r} />)}
          </div>
        </div>
      )}
    </section>
  )
}
