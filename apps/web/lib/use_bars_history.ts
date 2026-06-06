'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'

import { fetchBarsHistory } from '@/lib/symbol_api'
import { isMarketOpenNow, type Market } from '@/lib/markets'
import type { BarDTO, Interval, ResponseMeta } from '@/lib/types'

const PAGE = 500
const EMPTY: BarDTO[] = []

// 多组 bar 按 ts 升序合并去重; 靠后的组在 ts 冲突时获胜 (实时尾部覆盖历史)。
// 后端 ts 是统一格式的 ISO UTC 串, 字典序 == 时间序, 可直接比较。
export function mergeBarsAsc(...groups: BarDTO[][]): BarDTO[] {
  const m = new Map<string, BarDTO>()
  for (const g of groups) for (const b of g) m.set(b.ts, b)
  return Array.from(m.values()).sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))
}

// "稳定前缀 + 尾部覆盖" 合并: hist(升序去重)是 append-only 的稳定历史,
// tail(实时尾部 / 最新页, 通常 1~few 根)只覆盖 hist 末尾少数几根。
// 取 tail 最小 ts 为切点, hist 中早于切点的前缀**原样保留**(不参与 sort),
// 只对 >= 切点的 hist 尾部 + tail 做小范围 mergeBarsAsc。
//
// 为何不直接 mergeBarsAsc(hist, tail): 那会把累积上万根的 hist 每次全量 Map+sort,
// SSE 每秒 tick 一次就重算一次 → 滑动/实时越久越卡。本函数把每 tick 的代价从
// O(n log n) 降到 O(k log k)(k = 尾部重叠根数, 通常个位数)。
// 前提: hist 已由 useBarsHistory 保证升序去重(可二分切点)。
export function mergeTail(hist: BarDTO[], tail: BarDTO[]): BarDTO[] {
  if (tail.length === 0) return hist            // SSE 未推 → 零开销, 引用不变
  if (hist.length === 0) return mergeBarsAsc(tail)
  let cutoff = tail[0].ts
  for (let i = 1; i < tail.length; i++) if (tail[i].ts < cutoff) cutoff = tail[i].ts
  // 二分找 hist 中第一个 ts >= cutoff 的下标
  let lo = 0, hi = hist.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (hist[mid].ts < cutoff) lo = mid + 1
    else hi = mid
  }
  if (lo >= hist.length) return [...hist, ...mergeBarsAsc(tail)]  // tail 全在 hist 之后
  const prefix = hist.slice(0, lo)              // 稳定前缀, 不参与 sort
  const overlap = mergeBarsAsc(hist.slice(lo), tail)
  return prefix.concat(overlap)
}

export interface BarsHistory {
  bars: BarDTO[]
  loading: boolean
  error: unknown
  loadingMore: boolean
  hasMore: boolean
  loadMore: () => void
  meta?: ResponseMeta
}

/**
 * K 线历史取数 (币安/TradingView 口径): 首屏拉最新一页 (REST 游标分页),
 * 用户向左滑到边界时再拉更早一页 prepend。永不全量加载 (5m 92 万根)。
 *
 * - head: SWR 拉最新一页 (盘中可轮询刷新最新一根); 所有市场通用
 * - older: 手动累积的更早页, loadMore 触发 prepend
 * - 实时尾部 (crypto SSE) 由调用方 merge 到 bars 之上, 不在本 hook
 */
export function useBarsHistory(
  symbol: string, interval: Interval, market: Market,
  { enabled, poll }: { enabled: boolean; poll: boolean },
): BarsHistory {
  const head = useSWR(
    enabled ? `barhist:${symbol}:${interval}:head` : null,
    () => fetchBarsHistory(symbol, interval, { limit: PAGE }),
    {
      refreshInterval: () => (poll && isMarketOpenNow(market) ? 60_000 : 0),
      revalidateOnFocus: false,
    },
  )

  const [older, setOlder] = useState<BarDTO[]>([])
  const [loadingMore, setLoadingMore] = useState(false)
  const [reachedFloor, setReachedFloor] = useState(false)
  const loadingRef = useRef(false)
  // older 归属的 symbol:interval —— 切换周期时旧 older 不能混进新周期 (useEffect 重置滞后一帧)
  const olderKeyRef = useRef('')
  const key = `${symbol}:${interval}`

  // symbol / interval 切换 → 清空更早页, 重置到顶标记
  useEffect(() => {
    setOlder([])
    setReachedFloor(false)
    loadingRef.current = false
    olderKeyRef.current = key
  }, [key])

  // older 若仍属于上一个 symbol:interval (重置 effect 尚未跑), 视为空, 杜绝跨周期 ts 混入
  const safeOlder = olderKeyRef.current === key ? older : EMPTY
  // SWR 切 key 后 head.data 自动变 undefined(新 key 无缓存, 不跨 key 残留), 故切周期时
  // headBars 自然为空; 不需额外 key 守卫(之前加 olderKeyRef 守卫会因其滞后一帧误伤, 已回退)。
  const headBars = head.data?.bars ?? EMPTY
  // older(更早页, 稳定前缀)+ head(最新页, 尾部) → 稳定前缀合并, 避免每次全量 sort
  const bars = useMemo(() => mergeTail(safeOlder, headBars), [safeOlder, headBars])

  const loadMore = useCallback(() => {
    if (!enabled || loadingRef.current || reachedFloor) return
    if (olderKeyRef.current !== key) return  // 周期切换重置未完成, 不翻页 (避免跨周期 cursor)
    const cursor = bars[0]?.ts
    if (!cursor) return
    loadingRef.current = true
    setLoadingMore(true)
    fetchBarsHistory(symbol, interval, { before: cursor, limit: PAGE })
      .then((resp) => {
        if (olderKeyRef.current !== key) return  // 翻页途中切了周期, 丢弃结果
        const got = resp.bars ?? []
        if (got.length === 0) {
          setReachedFloor(true)
          return
        }
        setOlder((prev) => mergeBarsAsc(got, prev))
        if (got.length < PAGE) setReachedFloor(true)  // 不足一页 = 已到上市首日
      })
      .catch(() => { /* 翻页失败静默, 下次滑动重试 */ })
      .finally(() => {
        loadingRef.current = false
        setLoadingMore(false)
      })
  }, [enabled, reachedFloor, bars, symbol, interval, key])

  return {
    bars,
    loading: head.isLoading && !head.data,
    error: head.error,
    loadingMore,
    hasMore: enabled && !reachedFloor && bars.length > 0,
    loadMore,
    meta: head.data?.meta,
  }
}
