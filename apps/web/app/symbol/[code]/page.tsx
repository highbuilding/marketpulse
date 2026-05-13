'use client'

import { useMemo, useState } from 'react'
import useSWR from 'swr'

import { IntradayChart } from '@/components/IntradayChart'
import { KLineChart } from '@/components/KLineChart'
import { FundFlowPanel } from '@/components/FundFlowPanel'
import { fetchBars, fetchSymbolProfile } from '@/lib/symbol_api'
import type { Interval } from '@/lib/types'

const INTERVALS: { key: Interval; label: string }[] = [
  { key: '1m', label: '分时' },
  { key: '5m', label: '5分' },
  { key: '15m', label: '15分' },
  { key: '60m', label: '60分' },
  { key: '1d', label: '日线' },
  { key: '1wk', label: '周线' },
  { key: '1mo', label: '月线' },
]

export default function SymbolPage({ params }: { params: { code: string } }) {
  const symbol = decodeURIComponent(params.code)
  const [interval, setInterval] = useState<Interval>('1m')

  const { data: profile } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))

  const isIntraday = ['1m', '5m', '15m', '30m', '60m'].includes(interval)
  // 日/周/月线:从 2020-01-01 至今,确保至少覆盖 6 年
  const daysSinceY2020 = Math.ceil((Date.now() - new Date('2020-01-01').getTime()) / 86_400_000)
  const days = interval === '1m' ? 1 : isIntraday ? 5 : daysSinceY2020

  const { data, error, isLoading } = useSWR(
    `bars:${symbol}:${interval}:${days}`,
    () => fetchBars(symbol, interval, days),
    { refreshInterval: interval === '1m' ? 30_000 : 60_000 },
  )

  // 分时模式:拉日线最后一根作 prevClose,只展示当日
  const { data: daily } = useSWR(
    interval === '1m' ? `bars:${symbol}:1d:5` : null,
    () => fetchBars(symbol, '1d', 5),
  )
  const todayBars = useMemo(() => {
    if (!data || interval !== '1m' || data.bars.length === 0) return data?.bars ?? []
    // 取最后一根 bar 的北京时间日期,过滤同一交易日
    const lastBar = data.bars[data.bars.length - 1]
    const lastDate = new Date(lastBar.ts).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
    return data.bars.filter((b) => {
      const bDate = new Date(b.ts).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
      return bDate === lastDate
    })
  }, [data, interval])

  const prevClose = useMemo(() => {
    if (!daily || daily.bars.length < 2) return null
    // 倒数第二根日线作昨收(最后一根可能就是今日实时不准)
    return daily.bars[daily.bars.length - 2]?.close ?? daily.bars[daily.bars.length - 1]?.close
  }, [daily])

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-4">
          <h1 className="text-2xl font-bold">{profile?.name ?? '—'}</h1>
          <span className="text-lg font-mono text-neutral-400">{symbol}</span>
          {profile?.market && (
            <span className="text-xs text-neutral-500 uppercase">{profile.market}</span>
          )}
        </div>
        <a href="/market" className="text-xs text-neutral-400 hover:text-neutral-200">← 市场</a>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <div className="flex gap-1 mb-3">
          {INTERVALS.map((iv) => (
            <button
              key={iv.key}
              onClick={() => setInterval(iv.key)}
              className={`px-2 py-1 text-xs rounded ${interval === iv.key ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'}`}
            >
              {iv.label}
            </button>
          ))}
        </div>
        {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
        {error && <p className="text-sm text-red-400">加载失败:{String(error)}</p>}

        {/* 分时模式 */}
        {interval === '1m' && data && todayBars.length > 0 && (
          <IntradayChart bars={todayBars} prevClose={prevClose} height={420} />
        )}
        {interval === '1m' && data && todayBars.length === 0 && (
          <p className="text-sm text-yellow-400">当日无分时数据(可能未开盘或源不通)。</p>
        )}

        {/* 其他周期:K 线 */}
        {interval !== '1m' && data && data.bars.length > 0 && (
          <KLineChart bars={data.bars} height={420} />
        )}
        {interval !== '1m' && data && data.bars.length === 0 && (
          <p className="text-sm text-yellow-400">无数据。请先 <code>make warmup</code> 或本周期还未抓取。</p>
        )}
      </section>

      <FundFlowPanel symbol={symbol} />
    </main>
  )
}
