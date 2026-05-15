'use client'

import { useEffect, useMemo, useState } from 'react'
import useSWR, { useSWRConfig } from 'swr'

import { SignalsTable } from '@/components/SignalsTable'
import { fetchWatchlistEvents } from '@/lib/cd_signals_api'
import { allSignalTabs } from '@/lib/intervals'
import { fetchSymbolProfiles } from '@/lib/symbol_api'
import { bjtDateKey, effectiveTsIso, todayBjtKey } from '@/lib/signal_time'
import type { AnySignalInterval, CDSignalDTO } from '@/lib/types'

const ALL_TABS = allSignalTabs()

function isCrypto(sym: string): boolean {
  return !/\.(SH|SZ|BJ|HK)$/.test(sym) && sym.includes('/')
}

export function WatchlistSignalsPanel({ symbols }: { symbols: string[] }) {
  const { mutate: mutateGlobal } = useSWRConfig()

  // 仅在 watchlist 含 crypto 标的时才展示 4h tab(股票市场 4h ≡ 日线)
  const tabs = useMemo(() => {
    const hasCrypto = symbols.some(isCrypto)
    return ALL_TABS.filter((t) => t.key !== '4h' || hasCrypto)
  }, [symbols])

  const [interval, setInterval] = useState<AnySignalInterval>('1d')
  const activeInterval: AnySignalInterval = useMemo(
    () => (tabs.find((t) => t.key === interval) ? interval : '1d'),
    [interval, tabs],
  )

  const { data, isLoading } = useSWR(
    `wl:events:${activeInterval}:${symbols.join(',')}`,
    () => fetchWatchlistEvents(activeInterval, 100),
    { refreshInterval: 30_000 },
  )

  const signals = data?.signals ?? []

  // 信号到达后, 批量取一次 profile 并预填 SWR cache;
  // SignalsTable 内的 SymbolNameCell 用同一 key 直接命中, 消除 N+1。
  useEffect(() => {
    if (signals.length === 0) return
    const unique = Array.from(new Set(signals.map((s) => s.symbol)))
    let cancelled = false
    fetchSymbolProfiles(unique).then((profiles) => {
      if (cancelled) return
      for (const p of profiles) {
        mutateGlobal(`profile:${p.symbol}`, p, { revalidate: false })
      }
    }).catch(() => { /* 静默, 单查 fallback 仍然可用 */ })
    return () => { cancelled = true }
  }, [signals, mutateGlobal])

  // 切分当天 vs 历史(按北京时间自然日;1d 信号已 normalize 回"收盘当日")
  const { today, history } = useMemo(() => {
    const tk = todayBjtKey()
    const today: CDSignalDTO[] = []
    const history: CDSignalDTO[] = []
    for (const s of signals) {
      const key = bjtDateKey(effectiveTsIso(s.bar_ts, activeInterval))
      ;(key === tk ? today : history).push(s)
    }
    return { today, history }
  }, [signals, activeInterval])

  return (
    <section className="rounded-lg border border-neutral-800 p-4 bg-neutral-950">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-lg font-semibold">CD 信号事件流</h2>
        <span className="text-xs text-neutral-500">
          当天 {today.length} · 历史 {history.length}
        </span>
      </div>

      <div className="flex gap-1 mb-3">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setInterval(t.key)}
            className={`px-2 py-1 text-xs rounded ${
              activeInterval === t.key
                ? 'bg-neutral-700 text-white'
                : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isLoading && !data && (
        <p className="text-sm text-neutral-500">加载中…</p>
      )}

      {data && (
        <div className="space-y-4">
          <div>
            <h3 className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
              当天(BJT)
            </h3>
            {today.length === 0 ? (
              <p className="text-sm text-neutral-600">当天暂无信号。</p>
            ) : (
              <SignalsTable signals={today} interval={activeInterval} showSymbol />
            )}
          </div>

          <div>
            <h3 className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
              历史
            </h3>
            {history.length === 0 ? (
              <p className="text-sm text-neutral-600">暂无历史信号。</p>
            ) : (
              <SignalsTable signals={history} interval={activeInterval} showSymbol />
            )}
          </div>
        </div>
      )}
    </section>
  )
}
