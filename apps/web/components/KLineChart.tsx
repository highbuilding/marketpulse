'use client'

import {
  createChart, IChartApi, CandlestickData, HistogramData, Time,
  ISeriesApi, SeriesMarker,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'

import type { BarDTO, Interval } from '@/lib/types'

export interface SignalMarker {
  ts: string                 // ISO UTC, 与 bar.ts 同源
  signal_type: 'buy' | 'sell'
}

export interface KLineChartProps {
  bars: BarDTO[]
  interval: Interval
  height?: number
  signals?: SignalMarker[]
}

const INTRADAY: ReadonlySet<Interval> = new Set(['1m', '5m', '15m', '30m', '60m'])

function toBeijingDateStr(iso: string): string {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
}

function toBarTime(iso: string, interval: Interval): Time {
  if (INTRADAY.has(interval)) {
    return ((new Date(iso).getTime() / 1000) + 8 * 3600) as Time
  }
  return toBeijingDateStr(iso) as Time
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

export function KLineChart({ bars, interval, height = 400, signals }: KLineChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const stats = useMemo(() => computeStats(bars), [bars])
  const intraday = INTRADAY.has(interval)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: '#0a0a0a' }, textColor: '#d4d4d4' },
      grid: { vertLines: { color: '#262626' }, horzLines: { color: '#262626' } },
      timeScale: {
        timeVisible: intraday,
        secondsVisible: false,
        borderColor: '#262626',
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
      time: toBarTime(b.ts, interval),
      open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    const volData: HistogramData[] = bars.map((b) => ({
      time: toBarTime(b.ts, interval),
      value: b.volume,
      color: b.close >= b.open ? '#22c55e44' : '#ef444444',
    }))
    candle.setData(candleData)
    volume.setData(volData)

    if (signals && signals.length > 0) {
      const markers: SeriesMarker<Time>[] = signals.map((s) => ({
        time: toBarTime(s.ts, interval),
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

    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      candleRef.current = null
    }
  }, [bars, height, interval, intraday, signals])

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
