'use client'

import { createChart, IChartApi, LineData } from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import useSWR from 'swr'
import clsx from 'clsx'

import { fetchIndexMinute } from '@/lib/symbol_api'
import { formatAmount, formatFundInflow, formatRatioPct } from '@/lib/format_market'
import { inferMarket, isMarketOpenNow } from '@/lib/markets'
import { StaleBadge } from './StaleBadge'

function MiniChart({ points, color }: { points: { ts: string; close: number }[]; color: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!ref.current || points.length === 0) return
    const chart = createChart(ref.current, {
      height: 80,
      layout: {
        background: { color: 'transparent' }, textColor: '#737373',
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      timeScale: { visible: false },
      rightPriceScale: { visible: false },
      handleScale: false,
      handleScroll: false,
    })
    chartRef.current = chart
    const series = chart.addLineSeries({
      color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    })
    const data: LineData[] = points.map((p) => ({
      // +8h 让北京时间正确显示
      time: ((new Date(p.ts).getTime() / 1000) + 8 * 3600) as any,
      value: p.close,
    }))
    series.setData(data)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [points, color])

  return <div ref={ref} className="w-full" style={{ height: 80 }} />
}

export function IndexCard({ symbol }: { symbol: string }) {
  // A 股指数:5min 当日;港股指数:近 30 日日线 — 后端自动决定
  const isHK = symbol.endsWith('.HK')
  const days = isHK ? 30 : 1
  const market = inferMarket(symbol)
  const baseInterval = isHK ? 5 * 60_000 : 30_000
  const { data, isLoading, error } = useSWR(
    `index:${symbol}:${days}`, () => fetchIndexMinute(symbol, days),
    { refreshInterval: () => isMarketOpenNow(market) ? baseInterval : 0 },
  )

  let lastClose: number | null = null
  let changePct: number | null = null
  if (data && data.points.length > 0) {
    lastClose = data.points[data.points.length - 1].close
    // 优先用昨收作基准 (今日涨跌口径); 缺失时退到序列首点 (近 30 日日线场景或冷启动)
    const base = data.prev_close ?? data.points[0].close
    if (base > 0 && lastClose != null) changePct = ((lastClose - base) / base) * 100
  }
  const up = (changePct ?? 0) >= 0
  const color = up ? '#ef4444' : '#22c55e'

  return (
    <a
      href={`/symbol/${encodeURIComponent(symbol)}`}
      className={clsx(
        'block rounded-lg border border-neutral-800 bg-neutral-950 p-4 hover:border-neutral-600 transition-colors',
        data?.meta?.stale && 'opacity-60',
      )}
    >
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <div className="flex items-center gap-2">
            <div className="text-sm text-neutral-400">{data?.name || '加载中'}</div>
            <StaleBadge meta={data?.meta} />
          </div>
          <div className="font-mono text-xs text-neutral-500">{symbol}</div>
        </div>
        <div className="text-right">
          <div className={clsx('text-xl font-bold tabular-nums', up ? 'text-red-400' : 'text-green-400')}>
            {lastClose != null ? lastClose.toFixed(2) : '—'}
          </div>
          <div className={clsx('text-xs tabular-nums', up ? 'text-red-400' : 'text-green-400')}>
            {changePct != null ? `${up ? '+' : ''}${changePct.toFixed(2)}%` : '—'}
          </div>
        </div>
      </div>
      {isLoading && <p className="text-xs text-neutral-500">…</p>}
      {error && <p className="text-xs text-red-400">加载失败</p>}
      {data && data.points.length > 0 && <MiniChart points={data.points} color={color} />}
      {data && data.points.length === 0 && (
        <p className="text-xs text-neutral-500" style={{ height: 80 }}>无分时数据</p>
      )}
      <div className="text-[10px] text-neutral-600 mt-1">
        {data?.granularity === '5m' || data?.granularity === '1m' ? '当日分时' : '近 30 日日线'}
      </div>
      {data?.market_extras?.fund_inflow != null && data?.market_extras?.fund_inflow_label && (
        <div className="text-xs text-neutral-400 mt-1 tabular-nums">
          {data.market_extras.fund_inflow_label}: <span className={clsx(
            data.market_extras.fund_inflow >= 0 ? 'text-red-400' : 'text-green-400',
          )}>{formatFundInflow(data.market_extras.fund_inflow)}</span>
        </div>
      )}
      {data?.market_extras?.amount != null && data?.market_extras?.amount_unit && (
        <div className="text-xs text-neutral-400 tabular-nums">
          成交 {formatAmount(data.market_extras.amount, data.market_extras.amount_unit)}
          {data.market_extras.amount_ratio != null && (
            <span className={clsx(
              'ml-1',
              data.market_extras.amount_ratio >= 0 ? 'text-red-400' : 'text-green-400',
            )}>
              同比 {formatRatioPct(data.market_extras.amount_ratio)}
            </span>
          )}
        </div>
      )}
    </a>
  )
}
