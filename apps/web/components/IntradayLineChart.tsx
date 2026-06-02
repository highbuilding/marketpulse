'use client'

// 分时折线图组件。
// - 价格线(白色)+ 均价线(黄色 #FFD700)。
// - 数据来自 useIntradayLine(REST 首屏 + SSE 实时追加)。
// - time 用 UNIX 秒时间戳(ISO ts → Math.floor(Date.parse(ts)/1000)),
//   与 IntradayChart 的 toChartTime 保持一致(分时不加市场时区偏移,直接 UTC 秒)。
// - 容器 ref + ResizeObserver + cleanup,参考 KLineChart / IntradayChart 约定。

import {
  createChart,
  IChartApi,
  ISeriesApi,
  LineData,
  LineStyle,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import { inferMarket } from '@/lib/markets'
import { useIntradayLine } from '@/lib/use_intraday_line'

export interface IntradayLineChartProps {
  symbol: string
  height?: number
  enabled?: boolean
}

// ISO UTC ts → UNIX 秒(lightweight-charts UTCTimestamp)
// 分时图直接用 UTC 秒,不加市场时区偏移(与 IntradayChart.toChartTime 同口径)
function toChartTime(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000)
}

// lightweight-charts setData 要求 time 严格升序且无重复,兜底去重
function dedupAsc(rows: LineData[]): LineData[] {
  const map = new Map<number, LineData>()
  for (const r of rows) map.set(r.time as number, r)
  return Array.from(map.values()).sort((a, b) => (a.time as number) - (b.time as number))
}

export function IntradayLineChart({ symbol, height = 380, enabled = true }: IntradayLineChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const priceSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const avgSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const prevCloseSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const didFitRef = useRef(false)

  const { points, stale, prevClose, realtime } = useIntradayLine(symbol, enabled)

  // Effect 1: 创建 chart + series。height 变化时重建。
  useEffect(() => {
    if (!ref.current) return

    const chart = createChart(ref.current, {
      height,
      layout: {
        background: { color: '#0a0a0a' },
        textColor: '#a3a3a3',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: '#1f1f1f' },
        horzLines: { color: '#1f1f1f' },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#262626',
      },
      rightPriceScale: { borderColor: '#262626' },
    })
    chartRef.current = chart

    priceSeriesRef.current = chart.addLineSeries({
      color: '#f5f5f5',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    })

    avgSeriesRef.current = chart.addLineSeries({
      color: '#FFD700',
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: true,
      title: '均价',
    })

    // 昨收基准线(灰色虚线,水平覆盖当日时间轴)
    prevCloseSeriesRef.current = chart.addLineSeries({
      color: '#6b7280',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
    })

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    didFitRef.current = false

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      priceSeriesRef.current = null
      avgSeriesRef.current = null
      prevCloseSeriesRef.current = null
      didFitRef.current = false
    }
  }, [height])

  // Effect 2: 数据更新 → setData(全量,分时点数量有限,全量 setData 足够)
  useEffect(() => {
    const priceSeries = priceSeriesRef.current
    const avgSeries = avgSeriesRef.current
    const chart = chartRef.current
    if (!priceSeries || !avgSeries || !chart) return
    if (points.length === 0) return

    const priceData: LineData[] = points.map((p) => ({
      time: toChartTime(p.ts) as LineData['time'],
      value: p.price,
    }))
    const avgData: LineData[] = points
      .filter((p) => p.avg_price > 0)
      .map((p) => ({
        time: toChartTime(p.ts) as LineData['time'],
        value: p.avg_price,
      }))

    priceSeries.setData(dedupAsc(priceData))
    avgSeries.setData(dedupAsc(avgData))

    // 价格线染色:末价 ≥ 昨收 → 红(A 股口径);< 昨收 → 绿;无昨收 → 白
    if (prevClose != null && priceData.length > 0) {
      const last = priceData[priceData.length - 1].value as number
      const color = last >= prevClose ? '#ef4444' : '#22c55e'
      priceSeries.applyOptions({ color })
    } else {
      priceSeries.applyOptions({ color: '#f5f5f5' })
    }

    // 昨收基准线:覆盖当日时间轴首尾两点的水平虚线
    if (prevClose != null && points.length > 0) {
      const t0 = toChartTime(points[0].ts) as LineData['time']
      const t1 = toChartTime(points[points.length - 1].ts) as LineData['time']
      prevCloseSeriesRef.current?.setData([
        { time: t0, value: prevClose },
        { time: t1, value: prevClose },
      ])
    }

    // 仅首次 fit,后续刷新保留用户缩放/平移
    if (!didFitRef.current) {
      chart.timeScale().fitContent()
      didFitRef.current = true
    }
  }, [points, prevClose])

  return (
    <div className="w-full">
      {stale && (
        <div className="text-xs text-neutral-500 mb-1">数据可能陈旧</div>
      )}
      {inferMarket(symbol) === 'us' && !realtime && (
        <div className="text-xs text-amber-400 mb-1">超出实时名额,分时暂不实时更新(K 线正常)</div>
      )}
      <div ref={ref} className="w-full" />
      {inferMarket(symbol) === 'us' && (
        <div className="text-xs text-neutral-600 mt-1">成交量为 IEX 口径(免费层),仅供参考</div>
      )}
    </div>
  )
}
