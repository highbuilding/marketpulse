'use client'

import {
  createChart, IChartApi, CandlestickData, HistogramData, Time,
  ISeriesApi, SeriesMarker, IPriceLine, LineStyle,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'

import { isMarketOpenNow, marketTz, tzOffsetSeconds, type Market } from '@/lib/markets'
import { makeChartCrosshairFormatter, makeChartTickFormatter } from '@/lib/chart_time'
import { intervalSeconds } from '@/lib/intervals'
import type { BarDTO, Interval } from '@/lib/types'
import type { ResponseMeta } from '@/lib/types'
import { StaleBadge } from './StaleBadge'

export interface SignalMarker {
  ts: string                 // ISO UTC, 与 bar.ts 同源
  signal_type: 'buy' | 'sell'
}

export interface KLineChartProps {
  bars: BarDTO[]
  interval: Interval
  market: Market
  height?: number
  signals?: SignalMarker[]
  livePrice?: number | null   // 当前最新价 (来自 1m 分时), 在图上画一条水平虚线 + 右轴标签
  chipLevels?: {
    avgCost?: number | null
    cost70Low?: number | null
    cost70High?: number | null
    cost90Low?: number | null
    cost90High?: number | null
  } | null
  meta?: ResponseMeta
}

const INTRADAY: ReadonlySet<Interval> = new Set(['1m', '5m', '15m', '30m', '60m', '4h'])

function toBarTime(iso: string, interval: Interval, market: Market): Time {
  if (INTRADAY.has(interval)) {
    return ((new Date(iso).getTime() / 1000) + tzOffsetSeconds(market, iso)) as Time
  }
  // 日线/周线/月线: 按市场口径切日历
  // - crypto: ts 已是 UTC 自然日 open (5/29T00:00Z = 5/29 这根), 直接 slice(0,10) 即得标签
  // - 其他: 按市场时区切, ts = BJT/ET 自然日 00:00 直接 toLocaleDateString
  if (market === 'crypto') {
    return new Date(iso).toISOString().slice(0, 10) as Time
  }
  return new Date(iso).toLocaleDateString('en-CA',
    { timeZone: marketTz(market) }) as Time
}

function fmtPrice(v: number): string {
  if (!Number.isFinite(v)) return '—'
  return v >= 100 ? v.toFixed(2) : v >= 1 ? v.toFixed(3) : v.toFixed(4)
}

// lightweight-charts setData 要求 time 严格升序且无重复.
// 在 KLineChart 边界统一兜底: 同 time 后写覆盖, 然后升序.
// 这一道防御覆盖所有上游路径 (SSE / SWR / placeholder / strict mode race / interval 切换 race).
function dedupAscByTime<T extends { time: Time }>(rows: T[]): T[] {
  const map = new Map<string, T>()
  for (const r of rows) map.set(String(r.time), r)
  return Array.from(map.values()).sort((a, b) => {
    if (a.time < b.time) return -1
    if (a.time > b.time) return 1
    return 0
  })
}

function fmtVolume(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return v.toLocaleString()
}

interface Stats {
  last: number
  first: number
  diff: number
  pct: number
  high: number
  low: number
  vol: number
}

function computeStats(bars: BarDTO[]): Stats | null {
  if (bars.length === 0) return null
  const first = bars[0].open
  const last = bars[bars.length - 1].close
  const diff = last - first
  const pct = first !== 0 ? (diff / first) * 100 : 0
  let high = -Infinity
  let low = Infinity
  let vol = 0
  for (const b of bars) {
    if (b.high > high) high = b.high
    if (b.low < low) low = b.low
    vol += b.volume
  }
  return { last, first, diff, pct, high, low, vol }
}

export function KLineChart({
  bars, interval, market, height = 400, signals, livePrice, chipLevels, meta,
}: KLineChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const priceLineRefsRef = useRef<IPriceLine[]>([])
  const didFitRef = useRef(false)
  const stats = useMemo(() => computeStats(bars), [bars])
  const intraday = INTRADAY.has(interval)

  // 每 30 秒 tick 一次, 让 placeholder 即使在 bars 不变(SWR 命中)的情况下也能随时间扩张
  const [ticker, setTicker] = useState(0)
  useEffect(() => {
    if (!intraday || interval === '1m') return
    const id = setInterval(() => setTicker((n) => n + 1), 30_000)
    return () => clearInterval(id)
  }, [intraday, interval])

  // Effect 1: 创建 chart + series. 只在 height/interval/market 变 (interval 切换需要
  // 重建 timeScale 配置) 时重建. bars/signals/livePrice 等数据更新走下面的 effect 单独
  // setData / setMarkers / priceLines, 不重建 chart → 用户视野不会被强制拉回末端.
  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: {
        background: { color: '#0a0a0a' }, textColor: '#d4d4d4',
        attributionLogo: false,
      },
      grid: { vertLines: { color: '#262626' }, horzLines: { color: '#262626' } },
      localization: { timeFormatter: makeChartCrosshairFormatter(market) },
      timeScale: {
        timeVisible: intraday,
        secondsVisible: false,
        borderColor: '#262626',
        tickMarkFormatter: makeChartTickFormatter(market),
      },
      rightPriceScale: { borderColor: '#262626' },
    })
    chartRef.current = chart
    const candle = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })
    candleRef.current = candle
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: '',
      color: '#525252',
    })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
    volumeRef.current = volume
    didFitRef.current = false  // 新建 chart, 等下次 setData 时 fit 一次

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => {
      ro.disconnect()
      for (const line of priceLineRefsRef.current) {
        try { candle.removePriceLine(line) } catch { /* chart 已销毁可忽略 */ }
      }
      priceLineRefsRef.current = []
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
    }
  }, [height, interval, market, intraday])

  // Effect 2: setData. bars / placeholder ticker / livePrice (影响占位末根) 变化时
  // 仅 setData, 不动 chart 主体, 用户当前 visibleRange 保留.
  useEffect(() => {
    const candle = candleRef.current
    const volume = volumeRef.current
    if (!candle || !volume) return

    const candleData: CandlestickData[] = bars.map((b) => ({
      time: toBarTime(b.ts, interval, market),
      open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    const volData: HistogramData[] = bars.map((b) => ({
      time: toBarTime(b.ts, interval, market),
      value: b.volume,
      color: b.close >= b.open ? '#22c55e44' : '#ef444444',
    }))

    // 占位 bar (intraday + 1d, 不含 1m): 末根真实 bar 已收盘后, 后续 bucket 用占位
    if (intraday && interval !== '1m' && bars.length > 0 && isMarketOpenNow(market)) {
      const lastBar = bars[bars.length - 1]
      const lastCloseMs = new Date(lastBar.ts).getTime()
      const intervalMs = intervalSeconds(interval) * 1000
      const nowMs = Date.now()
      const MAX_PLACEHOLDERS = 6
      let nextCloseMs = lastCloseMs + intervalMs
      const placeholdersToAdd: number[] = []
      while (nowMs >= nextCloseMs && placeholdersToAdd.length < MAX_PLACEHOLDERS - 1) {
        placeholdersToAdd.push(nextCloseMs)
        nextCloseMs += intervalMs
      }
      if (placeholdersToAdd.length < MAX_PLACEHOLDERS) {
        placeholdersToAdd.push(nextCloseMs)
      }
      if (placeholdersToAdd.length > 0) {
        let gHigh = -Infinity
        let gLow = Infinity
        for (const b of bars) {
          if (b.high > gHigh) gHigh = b.high
          if (b.low < gLow) gLow = b.low
        }
        const grayTransparent = '#73737344'
        const lastIdx = placeholdersToAdd.length - 1

        for (let i = 0; i < placeholdersToAdd.length; i++) {
          const ms = placeholdersToAdd[i]
          const placeholderIso = new Date(ms).toISOString()
          const placeholderTime = toBarTime(placeholderIso, interval, market)
          const isCurrent = (i === lastIdx)

          if (isCurrent && livePrice !== undefined && livePrice !== null && Number.isFinite(livePrice)) {
            const openPrice = lastBar.close
            const isUp = livePrice >= openPrice
            const liveColor = isUp ? '#22c55e' : '#ef4444'
            candleData.push({
              time: placeholderTime,
              open: openPrice,
              high: Math.max(openPrice, livePrice),
              low: Math.min(openPrice, livePrice),
              close: livePrice,
              color: liveColor,
              borderColor: '#fbbf24',
              wickColor: liveColor,
            } as CandlestickData)
            volData.push({
              time: placeholderTime,
              value: 0,
              color: `${liveColor}66`,
            })
          } else {
            candleData.push({
              time: placeholderTime,
              open: gLow, high: gHigh, low: gLow, close: gHigh,
              color: grayTransparent,
              borderColor: grayTransparent,
              wickColor: grayTransparent,
            } as CandlestickData)
            volData.push({
              time: placeholderTime,
              value: 0,
              color: grayTransparent,
            })
          }
        }
      }
    }

    candle.setData(dedupAscByTime(candleData))
    volume.setData(dedupAscByTime(volData))

    // 仅在第一次有数据时 fit 一次, 之后保留用户 visibleRange (鼠标拖看历史不会被强制拉回末端)
    if (!didFitRef.current && candleData.length > 0) {
      chartRef.current?.timeScale().fitContent()
      didFitRef.current = true
    }
  }, [bars, interval, market, intraday, livePrice, ticker])

  // Effect 3: signals 变化 → setMarkers
  useEffect(() => {
    const candle = candleRef.current
    if (!candle) return
    if (!signals || signals.length === 0) {
      candle.setMarkers([])
      return
    }
    const markers: SeriesMarker<Time>[] = signals.map((s) => ({
      time: toBarTime(s.ts, interval, market),
      position: s.signal_type === 'buy' ? 'belowBar' : 'aboveBar',
      color: s.signal_type === 'buy' ? '#ef4444' : '#22c55e',
      shape: s.signal_type === 'buy' ? 'arrowUp' : 'arrowDown',
      text: s.signal_type === 'buy' ? 'CD买' : 'CD卖',
    }))
    markers.sort((a, b) => {
      if (a.time < b.time) return -1
      if (a.time > b.time) return 1
      return 0
    })
    candle.setMarkers(markers)
  }, [signals, interval, market])

  // Effect 4: livePrice / chipLevels 变化 → 重建 priceLines (现价虚线 + 筹码层级线)
  useEffect(() => {
    const candle = candleRef.current
    if (!candle) return
    // 清旧 priceLines
    for (const line of priceLineRefsRef.current) {
      try { candle.removePriceLine(line) } catch { /* ignore */ }
    }
    priceLineRefsRef.current = []

    if (livePrice !== undefined && livePrice !== null && Number.isFinite(livePrice)) {
      priceLineRefsRef.current.push(candle.createPriceLine({
        price: livePrice,
        color: '#fbbf24',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: '现价',
      }))
    }
    if (chipLevels && !intraday) {
      const lines = [
        { price: chipLevels.avgCost, color: '#60a5fa', title: '平均成本', width: 2 as const },
        { price: chipLevels.cost70Low, color: '#a78bfa', title: '70%下沿', width: 1 as const },
        { price: chipLevels.cost70High, color: '#a78bfa', title: '70%上沿', width: 1 as const },
        { price: chipLevels.cost90Low, color: '#64748b', title: '90%下沿', width: 1 as const },
        { price: chipLevels.cost90High, color: '#64748b', title: '90%上沿', width: 1 as const },
      ]
      for (const line of lines) {
        if (line.price == null || !Number.isFinite(line.price)) continue
        priceLineRefsRef.current.push(candle.createPriceLine({
          price: line.price,
          color: line.color,
          lineWidth: line.width,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: line.title,
        }))
      }
    }
  }, [livePrice, chipLevels, intraday])

  return (
    <div className="w-full">
      {stats && (
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 mb-2 text-sm">
          <div className="flex items-baseline gap-2">
            <span className="text-neutral-500">最新</span>
            <span className="font-mono text-lg text-neutral-100">{fmtPrice(stats.last)}</span>
          </div>
          <div className={`flex items-baseline gap-2 font-mono ${stats.diff >= 0 ? 'text-red-400' : 'text-green-400'}`}>
            <span>{stats.diff >= 0 ? '+' : ''}{fmtPrice(stats.diff)}</span>
            <span>{stats.pct >= 0 ? '+' : ''}{stats.pct.toFixed(2)}%</span>
          </div>
          <div className="text-xs text-neutral-500">
            <span>区间高 <span className="font-mono text-neutral-300">{fmtPrice(stats.high)}</span></span>
            <span className="ml-3">区间低 <span className="font-mono text-neutral-300">{fmtPrice(stats.low)}</span></span>
            <span className="ml-3">成交 <span className="font-mono text-neutral-300">{fmtVolume(stats.vol)}</span></span>
          </div>
          <StaleBadge meta={meta} />
        </div>
      )}
      <div ref={ref} className="w-full" />
    </div>
  )
}
