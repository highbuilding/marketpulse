'use client'

import { createChart, IChartApi, CandlestickData, HistogramData } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import type { BarDTO } from '@/lib/types'

export interface KLineChartProps {
  bars: BarDTO[]
  height?: number
}

function toChartTime(iso: string): number {
  // ISO 是 UTC,加 8h 让 lightweight-charts 按 UTC 渲染显示成北京时间
  return (new Date(iso).getTime() / 1000) + 8 * 3600
}

export function KLineChart({ bars, height = 400 }: KLineChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: '#0a0a0a' }, textColor: '#d4d4d4' },
      grid: { vertLines: { color: '#262626' }, horzLines: { color: '#262626' } },
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart

    const candle = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: '',
      color: '#525252',
    })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

    const candleData: CandlestickData[] = bars.map((b) => ({
      time: toChartTime(b.ts) as any,
      open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    const volData: HistogramData[] = bars.map((b) => ({
      time: toChartTime(b.ts) as any,
      value: b.volume,
      color: b.close >= b.open ? '#22c55e44' : '#ef444444',
    }))
    candle.setData(candleData)
    volume.setData(volData)
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
  }, [bars, height])

  return <div ref={ref} className="w-full" />
}
