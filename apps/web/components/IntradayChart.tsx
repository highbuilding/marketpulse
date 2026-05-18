'use client'

import {
  createChart, IChartApi, ISeriesApi, IPriceLine,
  LineData, HistogramData, LineStyle,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'

import { tzOffsetSeconds, type Market } from '@/lib/markets'
import { makeChartCrosshairFormatter, makeChartTickFormatter } from '@/lib/chart_time'
import type { BarDTO } from '@/lib/types'

export interface IntradayChartProps {
  bars: BarDTO[]   // 当日 1min bars (按时间正序)
  market: Market
  height?: number
  prevClose?: number | null  // 昨收价,用于横线 reference
}

interface VWAPPoint {
  ts: string
  vwap: number
}

function computeVwap(bars: BarDTO[]): VWAPPoint[] {
  let cumPV = 0
  let cumVol = 0
  const out: VWAPPoint[] = []
  for (const b of bars) {
    // typical price = (high + low + close) / 3,逼近成交均价
    const tp = (b.high + b.low + b.close) / 3
    cumPV += tp * b.volume
    cumVol += b.volume
    const vwap = cumVol > 0 ? cumPV / cumVol : b.close
    out.push({ ts: b.ts, vwap })
  }
  return out
}

function toChartTime(iso: string, market: Market): number {
  // ISO 是 UTC,加市场时区偏移让 lightweight-charts 按 UTC 渲染时显示为市场本地时间
  return (new Date(iso).getTime() / 1000) + tzOffsetSeconds(market, iso)
}

export function IntradayChart({ bars, market, height = 380, prevClose }: IntradayChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const priceSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const volSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const prevCloseLineRef = useRef<IPriceLine | null>(null)
  const didFitRef = useRef(false)

  const vwap = useMemo(() => computeVwap(bars), [bars])

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: {
        background: { color: '#0a0a0a' }, textColor: '#a3a3a3',
        attributionLogo: false,
      },
      grid: { vertLines: { color: '#1f1f1f' }, horzLines: { color: '#1f1f1f' } },
      localization: { timeFormatter: makeChartCrosshairFormatter(market) },
      timeScale: {
        timeVisible: true, secondsVisible: false,
        tickMarkFormatter: makeChartTickFormatter(market),
      },
      rightPriceScale: { borderColor: '#262626' },
    })
    chartRef.current = chart

    priceSeriesRef.current = chart.addLineSeries({
      color: '#f5f5f5', lineWidth: 2,
      priceLineVisible: false,
    })
    vwapSeriesRef.current = chart.addLineSeries({
      color: '#fbbf24', lineWidth: 1, lineStyle: LineStyle.Solid,
      priceLineVisible: false,
    })
    volSeriesRef.current = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      color: '#525252',
    })
    volSeriesRef.current.priceScale().applyOptions({
      scaleMargins: { top: 0.75, bottom: 0 },
    })

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      priceSeriesRef.current = null
      vwapSeriesRef.current = null
      volSeriesRef.current = null
      prevCloseLineRef.current = null
      didFitRef.current = false
    }
  }, [height, market])

  useEffect(() => {
    const priceSeries = priceSeriesRef.current
    const vwapSeries = vwapSeriesRef.current
    const volSeries = volSeriesRef.current
    const chart = chartRef.current
    if (!priceSeries || !vwapSeries || !volSeries || !chart) return
    if (bars.length === 0) return

    const priceData: LineData[] = bars.map((b) => ({
      time: toChartTime(b.ts, market) as any,
      value: b.close,
    }))
    priceSeries.setData(priceData)

    vwapSeries.setData(vwap.map((p) => ({
      time: toChartTime(p.ts, market) as any,
      value: p.vwap,
    })))

    const volData: HistogramData[] = bars.map((b) => ({
      time: toChartTime(b.ts, market) as any,
      value: b.volume,
      color: prevClose != null && b.close >= prevClose ? '#ef444466' : '#22c55e66',
    }))
    volSeries.setData(volData)

    if (prevCloseLineRef.current) {
      priceSeries.removePriceLine(prevCloseLineRef.current)
      prevCloseLineRef.current = null
    }
    if (prevClose != null && prevClose > 0) {
      prevCloseLineRef.current = priceSeries.createPriceLine({
        price: prevClose,
        color: '#6b7280',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: '昨收',
      })
    }

    // 仅首次 fit,后续刷新保留用户的缩放/平移
    if (!didFitRef.current) {
      chart.timeScale().fitContent()
      didFitRef.current = true
    }
  }, [bars, vwap, prevClose, market])

  return <div ref={ref} className="w-full" />
}
