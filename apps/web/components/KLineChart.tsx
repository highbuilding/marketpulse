'use client'

import {
  createChart, IChartApi, CandlestickData, HistogramData, Time,
  ISeriesApi, SeriesMarker, IPriceLine, LineStyle,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'

import { marketTz, tzOffsetSeconds, type Market } from '@/lib/markets'
import { makeChartCrosshairFormatter, makeChartTickFormatter } from '@/lib/chart_time'
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
  // 滑动翻页 (历史懒加载, 币安口径): 可视区左边界逼近已加载最老一根时触发。
  onLoadMore?: () => void
  hasMore?: boolean
  loadingMore?: boolean
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

// 重建 time→bar 映射 (crosshair 悬停反查那根 OHLCV)。原地清空复用同一 Map 引用。
// 仅全量路径(setData)调用; 增量路径只 set 末根, 不走这里。
function rebuildBarIndex(
  m: Map<number, BarDTO>, bars: BarDTO[], interval: Interval, market: Market,
): void {
  m.clear()
  for (const b of bars) m.set(toBarTime(b.ts, interval, market) as number, b)
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
  onLoadMore, hasMore, loadingMore,
}: KLineChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const priceLineRefsRef = useRef<IPriceLine[]>([])
  const didFitRef = useRef(false)
  const prevLenRef = useRef(0)         // 上次 bars.length, 判断增量/全量
  const prevFirstTsRef = useRef<string | null>(null)  // 首 bar ts, 翻页/切周期检测
  const prevKeyRef = useRef('')        // 上次 interval:market, 检测切周期
  // 翻页回调/状态用 ref 持有, 避免 subscribe 闭包拿到过期值 (subscribe 只在建图时挂一次)
  const loadMoreRef = useRef<(() => void) | undefined>(onLoadMore)
  const hasMoreRef = useRef<boolean | undefined>(hasMore)
  const loadingMoreRef = useRef<boolean | undefined>(loadingMore)
  loadMoreRef.current = onLoadMore
  hasMoreRef.current = hasMore
  loadingMoreRef.current = loadingMore
  const intraday = INTRADAY.has(interval)
  // crosshair 时间格式化器: 按 market memo 一次, hover 浮窗 + 建图 localization 共用,
  // 避免鼠标移动每次重渲染都新建函数(hover 高频触发 setHoverBar)。
  const crosshairFmt = useMemo(() => makeChartCrosshairFormatter(market), [market])
  // 悬停某根 K 线时显示该根 OHLCV(crosshair move 跟随)
  const [hoverBar, setHoverBar] = useState<BarDTO | null>(null)
  const barByTimeRef = useRef<Map<number, BarDTO>>(new Map())

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

    // 滑动翻页: 可视区左边界逼近已加载最老一根 (logical from < 阈值) → 拉更早一页。
    // lightweight-charts 在 prepend setData 后按 bar-index 保留逻辑区间, 视野不跳。
    const onRange = (range: { from: number } | null) => {
      if (!range) return
      if (range.from > 10) return
      if (!hasMoreRef.current || loadingMoreRef.current) return
      loadMoreRef.current?.()
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange)

    // 悬停跟随: 鼠标移到哪根 → 显示该根 OHLCV(按 time 反查原始 bar)
    const onCrosshair = (param: { time?: Time }) => {
      if (param.time == null) { setHoverBar(null); return }
      const b = barByTimeRef.current.get(param.time as number)
      setHoverBar(b ?? null)
    }
    chart.subscribeCrosshairMove(onCrosshair)

    return () => {
      ro.disconnect()
      try { chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange) } catch { /* chart 已销毁 */ }
      try { chart.unsubscribeCrosshairMove(onCrosshair) } catch { /* chart 已销毁 */ }
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

  // Effect 2: 数据更新. 区分全量 setData 与增量 update:
  // - 切周期/翻页/首次 → setData (全量)
  // - SSE tick 更新末根 → update (O(1), 不中断用户交互)
  useEffect(() => {
    const candle = candleRef.current
    const volume = volumeRef.current
    if (!candle || !volume) return

    const len = bars.length
    const firstTs = bars[0]?.ts ?? null
    const key = `${interval}:${market}`
    const needsFull = prevLenRef.current === 0          // 首次
      || prevKeyRef.current !== key                     // 切周期/市场
      || prevFirstTsRef.current !== firstTs             // 翻页/切周期
      || Math.abs(len - prevLenRef.current) > 1         // 批量增删

    if (len === 0) {
      candle.setData([])
      volume.setData([])
      barByTimeRef.current = new Map()
    } else if (needsFull) {
      // 全量 setData
      const candleData: CandlestickData[] = bars.map((b) => ({
        time: toBarTime(b.ts, interval, market),
        open: b.open, high: b.high, low: b.low, close: b.close,
      }))
      const volData: HistogramData[] = bars.map((b) => ({
        time: toBarTime(b.ts, interval, market),
        value: b.volume,
        color: b.close >= b.open ? '#22c55e44' : '#ef444444',
      }))
      candle.setData(dedupAscByTime(candleData))
      volume.setData(dedupAscByTime(volData))
      if (!didFitRef.current || prevKeyRef.current !== key) {
        chartRef.current?.timeScale().fitContent()
        didFitRef.current = true
      }
      // 全量路径才重建 time→bar 映射(O(n)); 增量路径只 set 末根(下方)
      rebuildBarIndex(barByTimeRef.current, bars, interval, market)
    } else {
      // 增量: 只更新末根 (SSE tick/bar → 原地替换 或 追加一根)
      const last = bars[len - 1]
      const time = toBarTime(last.ts, interval, market)
      try {
        candle.update({ time, open: last.open, high: last.high, low: last.low, close: last.close } as CandlestickData)
        volume.update({ time, value: last.volume, color: last.close >= last.open ? '#22c55e44' : '#ef444444' } as HistogramData)
        // 增量只动末根 → time→bar 映射 O(1) 更新, 不重建整个 Map
        barByTimeRef.current.set(time as number, last)
      } catch {
        // update 失败 (bar 不存在) → 回退 setData + 全量重建映射
        candle.setData(dedupAscByTime(bars.map((b) => ({
          time: toBarTime(b.ts, interval, market),
          open: b.open, high: b.high, low: b.low, close: b.close,
        }))))
        volume.setData(dedupAscByTime(bars.map((b) => ({
          time: toBarTime(b.ts, interval, market),
          value: b.volume,
          color: b.close >= b.open ? '#22c55e44' : '#ef444444',
        }))))
        rebuildBarIndex(barByTimeRef.current, bars, interval, market)
      }
    }

    prevLenRef.current = len
    prevFirstTsRef.current = firstTs
    prevKeyRef.current = key
  }, [bars, interval, market])

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
    <div className="w-full relative">
      <div ref={ref} className="w-full" />
      {hoverBar && (
        <div className="absolute top-2 right-2 z-10 pointer-events-none rounded-md border border-neutral-700 bg-neutral-900/90 px-3 py-2 text-xs font-mono leading-relaxed shadow-lg">
          <div className="text-neutral-400 mb-1">{crosshairFmt(toBarTime(hoverBar.ts, interval, market))}</div>
          <div className="flex justify-between gap-4"><span className="text-neutral-500">开</span><span className="text-neutral-200">{fmtPrice(hoverBar.open)}</span></div>
          <div className="flex justify-between gap-4"><span className="text-neutral-500">高</span><span className="text-red-400">{fmtPrice(hoverBar.high)}</span></div>
          <div className="flex justify-between gap-4"><span className="text-neutral-500">低</span><span className="text-green-400">{fmtPrice(hoverBar.low)}</span></div>
          <div className="flex justify-between gap-4"><span className="text-neutral-500">收</span><span className={hoverBar.close >= hoverBar.open ? 'text-red-400' : 'text-green-400'}>{fmtPrice(hoverBar.close)}</span></div>
          <div className="flex justify-between gap-4"><span className="text-neutral-500">涨跌</span><span className={hoverBar.close >= hoverBar.open ? 'text-red-400' : 'text-green-400'}>{hoverBar.open ? `${hoverBar.close >= hoverBar.open ? '+' : ''}${(((hoverBar.close - hoverBar.open) / hoverBar.open) * 100).toFixed(2)}%` : '—'}</span></div>
          <div className="flex justify-between gap-4"><span className="text-neutral-500">量</span><span className="text-neutral-300">{fmtVolume(hoverBar.volume)}</span></div>
        </div>
      )}
    </div>
  )
}
