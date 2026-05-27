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
}

const INTRADAY: ReadonlySet<Interval> = new Set(['1m', '5m', '15m', '30m', '60m', '4h'])

function toBarTime(iso: string, interval: Interval, market: Market): Time {
  if (INTRADAY.has(interval)) {
    return ((new Date(iso).getTime() / 1000) + tzOffsetSeconds(market, iso)) as Time
  }
  // 日线: 按市场时区切日历
  return new Date(iso).toLocaleDateString('en-CA',
    { timeZone: marketTz(market) }) as Time
}

function fmtPrice(v: number): string {
  if (!Number.isFinite(v)) return '—'
  return v >= 100 ? v.toFixed(2) : v >= 1 ? v.toFixed(3) : v.toFixed(4)
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
  bars, interval, market, height = 400, signals, livePrice, chipLevels,
}: KLineChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const stats = useMemo(() => computeStats(bars), [bars])
  const intraday = INTRADAY.has(interval)

  // 每 30 秒 tick 一次, 让 placeholder 即使在 bars 不变(SWR 命中)的情况下也能随时间扩张
  const [ticker, setTicker] = useState(0)
  useEffect(() => {
    if (!intraday || interval === '1m') return
    const id = setInterval(() => setTicker((n) => n + 1), 30_000)
    return () => clearInterval(id)
  }, [intraday, interval])

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
    // - 已过期 bucket (close <= now, 但数据没拉到): 灰色撑满, 表示"应有数据但未到"
    // - 当前 bucket (close > now): 用 livePrice 渲染实时彩色蜡烛 (永远是最末根)
    // 关键: 仅在市场处于交易时段时显示 placeholder, 否则休市期间所有周期会无谓堆 6 根
    if (intraday && interval !== '1m' && bars.length > 0 && isMarketOpenNow(market)) {
      const lastBar = bars[bars.length - 1]
      const lastCloseMs = new Date(lastBar.ts).getTime()
      const intervalMs = intervalSeconds(interval) * 1000
      const nowMs = Date.now()
      const MAX_PLACEHOLDERS = 6
      let nextCloseMs = lastCloseMs + intervalMs
      const placeholdersToAdd: number[] = []
      // 已过期但数据没拉到的 bucket (close <= now)
      while (nowMs >= nextCloseMs && placeholdersToAdd.length < MAX_PLACEHOLDERS - 1) {
        placeholdersToAdd.push(nextCloseMs)
        nextCloseMs += intervalMs
      }
      // 永远再加一根"当前正在生成"的 bucket: close 时刻 > now (尚未结束)
      if (placeholdersToAdd.length < MAX_PLACEHOLDERS) {
        placeholdersToAdd.push(nextCloseMs)
      }
      if (placeholdersToAdd.length > 0) {
        // 计算所有真实 bar 的 high/low 全局范围, 给"已错过"placeholder 撑满高度
        let gHigh = -Infinity
        let gLow = Infinity
        for (const b of bars) {
          if (b.high > gHigh) gHigh = b.high
          if (b.low < gLow) gLow = b.low
        }
        const grayTransparent = '#73737344'  // 27% alpha 灰色
        const lastIdx = placeholdersToAdd.length - 1  // 最末根 = 最贴近 now = 当前正在生成的 bucket

        for (let i = 0; i < placeholdersToAdd.length; i++) {
          const ms = placeholdersToAdd[i]
          const placeholderIso = new Date(ms).toISOString()
          const placeholderTime = toBarTime(placeholderIso, interval, market)
          const isCurrent = (i === lastIdx)

          if (isCurrent && livePrice !== undefined && livePrice !== null && Number.isFinite(livePrice)) {
            // 当前 bucket: 用 livePrice 渲染实时彩色蜡烛 (open = 上根 close)
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
              borderColor: '#fbbf24',          // 琥珀边框, 醒目区分已收盘 bar
              wickColor: liveColor,
            } as CandlestickData)
            volData.push({
              time: placeholderTime,
              value: 0,
              color: `${liveColor}66`,
            })
          } else {
            // 已错过的 bucket (或没拿到 livePrice): 灰色撑满
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

    candle.setData(candleData)
    volume.setData(volData)

    if (signals && signals.length > 0) {
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
    }

    // 当前价位指针: livePrice 来自 1m 分时, 即使当前 interval 的 K 线还没收盘也能看到价位
    const priceLineRefs: IPriceLine[] = []
    if (livePrice !== undefined && livePrice !== null && Number.isFinite(livePrice)) {
      priceLineRefs.push(candle.createPriceLine({
        price: livePrice,
        color: '#fbbf24',          // amber-400, 醒目但不与涨绿/跌红冲突
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
        priceLineRefs.push(candle.createPriceLine({
          price: line.price,
          color: line.color,
          lineWidth: line.width,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: line.title,
        }))
      }
    }

    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => {
      ro.disconnect()
      for (const line of priceLineRefs) {
        try { candle.removePriceLine(line) } catch { /* chart 已销毁可忽略 */ }
      }
      chart.remove()
      chartRef.current = null
      candleRef.current = null
    }
  }, [bars, height, interval, intraday, signals, market, livePrice, chipLevels, ticker])

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
        </div>
      )}
      <div ref={ref} className="w-full" />
    </div>
  )
}
