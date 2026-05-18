'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'

import { SignalsTable } from '@/components/SignalsTable'
import { listCDSignalsBySymbol, scanCDSignals } from '@/lib/cd_signals_api'
import { detailSignalTabs } from '@/lib/intervals'
import { bjtDateKey, todayBjtKey } from '@/lib/signal_time'
import type { CDSignalDTO, DetailSignalInterval } from '@/lib/types'

const TABS = detailSignalTabs(null)

export function CDSignalPanel({ symbol }: { symbol: string }) {
  const [interval, setInterval] = useState<DetailSignalInterval>('60m')
  const triggered = useRef<Set<DetailSignalInterval>>(new Set())
  const [scanning, setScanning] = useState<DetailSignalInterval | null>(null)

  const swrKey = `cd-panel:${symbol}:${interval}`
  const { data, mutate, isLoading } = useSWR(
    swrKey,
    () => listCDSignalsBySymbol(symbol, [interval]),
    { refreshInterval: 30_000 },
  )

  useEffect(() => {
    if (triggered.current.has(interval)) return
    triggered.current.add(interval)
    let cancelled = false
    setScanning(interval)
    scanCDSignals({ symbols: [symbol], intervals: [interval] })
      .then(() => { if (!cancelled) mutate() })
      .catch(() => { /* 静默,列表仍展示已有 */ })
      .finally(() => { if (!cancelled) setScanning(null) })
    return () => { cancelled = true }
  }, [interval, symbol, mutate])

  const signals = data?.signals ?? []
  const isScanningThis = scanning === interval

  // 切分当天 vs 历史(按 BJT 自然日;adapter 已把 1d ts normalize 为 BJT 自然日 00:00)
  const { today, history } = useMemo(() => {
    const tk = todayBjtKey()
    const today: CDSignalDTO[] = []
    const history: CDSignalDTO[] = []
    for (const s of signals) {
      const key = bjtDateKey(s.bar_ts)
      ;(key === tk ? today : history).push(s)
    }
    return { today, history }
  }, [signals])

  return (
    <section className="rounded-lg border border-neutral-800 p-4 bg-neutral-950">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-lg font-semibold">CD 抄底/卖出信号</h2>
        <span className="text-xs text-neutral-500">
          {isScanningThis ? '扫描中…' : `当天 ${today.length} · 历史 ${history.length}`}
        </span>
      </div>

      <div className="flex gap-1 mb-3">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setInterval(t.key)}
            className={`px-2 py-1 text-xs rounded ${
              interval === t.key
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
      {!isLoading && signals.length === 0 && (
        <p className="text-sm text-neutral-500">
          {isScanningThis ? '首次扫描中,请稍候…' : '暂无信号。'}
        </p>
      )}

      {signals.length > 0 && (
        <div className="space-y-4">
          <div>
            <h3 className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
              当天(BJT)
            </h3>
            {today.length === 0 ? (
              <p className="text-sm text-neutral-600">当天暂无信号。</p>
            ) : (
              <SignalsTable signals={today} interval={interval} />
            )}
          </div>

          <div>
            <h3 className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
              历史
            </h3>
            {history.length === 0 ? (
              <p className="text-sm text-neutral-600">暂无历史信号。</p>
            ) : (
              <SignalsTable signals={history} interval={interval} />
            )}
          </div>
        </div>
      )}
    </section>
  )
}
