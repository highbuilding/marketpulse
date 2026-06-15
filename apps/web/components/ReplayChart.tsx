'use client'

// 盘后回放折线图 (受控)。
// - 数据完全来自 props (不接 SSE / REST), 父组件负责取数。
// - 单条价格线 + 可选昨收基准虚线; 末值 ≥ 昨收染红, < 昨收染绿 (A 股口径)。
// - createChart / ResizeObserver / cleanup 约定参考 IntradayLineChart。
// - time 用 UTC 秒 (ISO ts → Math.floor(Date.parse/1000)), 同 IntradayLineChart 口径。

import { createChart, IChartApi, ISeriesApi, LineData, LineStyle } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

export interface ReplayChartPoint {
  ts: string
  value: number
}

export interface ReplayChartProps {
  points: ReplayChartPoint[]
  baseline?: number | null
  height?: number
}

function toChartTime(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000)
}

function dedupAsc(rows: LineData[]): LineData[] {
  const map = new Map<number, LineData>()
  for (const r of rows) map.set(r.time as number, r)
  return Array.from(map.values()).sort((a, b) => (a.time as number) - (b.time as number))
}

export function ReplayChart({ points, baseline = null, height = 320 }: ReplayChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const priceSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const baseSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)

  // Effect 1: 创建 chart + series (height 变化重建)
  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: 'transparent' }, textColor: '#a3a3a3', attributionLogo: false },
      grid: { vertLines: { color: '#1f1f1f' }, horzLines: { color: '#1f1f1f' } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#262626' },
      rightPriceScale: { borderColor: '#262626' },
    })
    chartRef.current = chart
    priceSeriesRef.current = chart.addLineSeries({
      color: '#f5f5f5', lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
    })
    baseSeriesRef.current = chart.addLineSeries({
      color: '#6b7280', lineWidth: 1, lineStyle: LineStyle.Dashed,
      priceLineVisible: false, lastValueVisible: false,
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
      baseSeriesRef.current = null
    }
  }, [height])

  // Effect 2: 数据更新 → setData (回放数据量有限, 全量 setData)
  useEffect(() => {
    const priceSeries = priceSeriesRef.current
    const chart = chartRef.current
    if (!priceSeries || !chart) return
    if (points.length === 0) {
      priceSeries.setData([])
      baseSeriesRef.current?.setData([])
      return
    }
    const data = dedupAsc(points.map((p) => ({
      time: toChartTime(p.ts) as LineData['time'],
      value: p.value,
    })))
    priceSeries.setData(data)

    // 末值 vs 昨收染色
    if (baseline != null && data.length > 0) {
      const last = data[data.length - 1].value as number
      priceSeries.applyOptions({ color: last >= baseline ? '#ef4444' : '#22c55e' })
    } else {
      priceSeries.applyOptions({ color: '#f5f5f5' })
    }

    // 昨收基准线: 覆盖首尾两点水平虚线
    if (baseline != null && data.length > 0) {
      baseSeriesRef.current?.setData([
        { time: data[0].time, value: baseline },
        { time: data[data.length - 1].time, value: baseline },
      ])
    } else {
      baseSeriesRef.current?.setData([])
    }

    chart.timeScale().fitContent()
  }, [points, baseline])

  return <div ref={ref} style={{ width: '100%' }} />
}
