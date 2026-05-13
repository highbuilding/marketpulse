'use client'

import useSWR from 'swr'
import clsx from 'clsx'

import { fetchOverview } from '@/lib/api'
import type { AdapterHealth, Market, QuoteDTO } from '@/lib/types'
import { HealthBadge } from './HealthBadge'

const LABELS: Record<Market, string> = {
  ashare: 'A 股',
  hk: '港股',
  us: '美股',
  crypto: 'Crypto',
}

function QuoteRow({ q }: { q: QuoteDTO }) {
  const up = q.change_pct >= 0
  return (
    <div className="flex items-center justify-between text-sm py-1 border-b border-neutral-800">
      <span className="font-mono">{q.symbol}</span>
      <span className={clsx('tabular-nums', up ? 'text-green-400' : 'text-red-400')}>
        {q.price.toFixed(2)} ({up ? '+' : ''}{q.change_pct.toFixed(2)}%)
      </span>
    </div>
  )
}

export function MarketCard({ market, health }: { market: Market; health: AdapterHealth | undefined }) {
  const disabled = health?.state === 'disabled'
  const { data, error, isLoading } = useSWR(
    disabled ? null : `overview:${market}`,
    () => fetchOverview(market),
    { refreshInterval: 10_000 },
  )

  return (
    <section className={clsx(
      'rounded-lg border border-neutral-800 p-4 bg-neutral-950',
      disabled && 'opacity-40',
    )}>
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">{LABELS[market]}</h2>
        <HealthBadge health={health} />
      </header>

      {disabled && <p className="text-sm text-neutral-400">数据源未配置,已禁用</p>}
      {!disabled && isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {!disabled && error && <p className="text-sm text-red-400">加载失败</p>}
      {!disabled && data?.status === 'warming' && (
        <p className="text-sm text-yellow-400">数据预热中,几秒后刷新</p>
      )}
      {!disabled && data && data.status !== 'warming' && (
        <div className="space-y-3">
          {data.indices.length > 0 && (
            <div>
              <h3 className="text-xs text-neutral-400 uppercase mb-1">指数</h3>
              {data.indices.map((q) => <QuoteRow key={q.symbol} q={q} />)}
            </div>
          )}
          <div>
            <h3 className="text-xs text-neutral-400 uppercase mb-1">涨幅前列</h3>
            {data.top_gainers.slice(0, 5).map((q) => <QuoteRow key={q.symbol} q={q} />)}
          </div>
          <div>
            <h3 className="text-xs text-neutral-400 uppercase mb-1">跌幅前列</h3>
            {data.top_losers.slice(0, 5).map((q) => <QuoteRow key={q.symbol} q={q} />)}
          </div>
        </div>
      )}
    </section>
  )
}
