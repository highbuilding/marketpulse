'use client'

import { createChart, IChartApi, LineData, HistogramData, LineStyle } from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'

import type { BarDTO } from '@/lib/types'

export interface IntradayChartProps {
  bars: BarDTO[]   // 当日 1min bars (按时间正序)
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

function toChartTime(iso: string): number {
  // ISO 是 UTC,加 8h 偏移让 lightweight-charts 按 UTC 渲染时显示为北京时间
  return (new Date(iso).getTime() / 1000) + 8 * 3600
}

export function IntradayChart({ bars, height = 380, prevClose }: IntradayChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  const vwap = useMemo(() => computeVwap(bars), [bars])

  useEffect(() => {
    if (!ref.current || bars.length === 0) return
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: '#0a0a0a' }, textColor: '#a3a3a3' },
      grid: { vertLines: { color: '#1f1f1f' }, horzLines: { color: '#1f1f1f' } },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: '#262626' },
    })
    chartRef.current = chart

    const priceSeries = chart.addLineSeries({
      color: '#f5f5f5', lineWidth: 2,
      priceLineVisible: false,
    })
    const priceData: LineData[] = bars.map((b) => ({
      time: toChartTime(b.ts) as any,
      value: b.close,
    }))
    priceSeries.setData(priceData)

    const vwapSeries = chart.addLineSeries({
      color: '#fbbf24', lineWidth: 1, lineStyle: LineStyle.Solid,
      priceLineVisible: false,
    })
    vwapSeries.setData(vwap.map((p) => ({
      time: toChartTime(p.ts) as any,
      value: p.vwap,
    })))

    if (prevClose != null && prevClose > 0) {
      priceSeries.createPriceLine({
        price: prevClose,
        color: '#6b7280',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: '昨收',
      })
    }

    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      color: '#525252',
    })
    volSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.75, bottom: 0 },
    })
    const volData: HistogramData[] = bars.map((b) => ({
      time: toChartTime(b.ts) as any,
      value: b.volume,
      color: prevClose != null && b.close >= prevClose ? '#ef444466' : '#22c55e66',
    }))
    volSeries.setData(volData)

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
  }, [bars, vwap, height, prevClose])

  return <div ref={ref} className="w-full" />
}
