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
  // head.data 在切 key 后、新请求返回前会残留旧周期数据(SWR stale)。
  // 守卫: olderKeyRef(随 key 同步重置)!= key 或正在加载新 key 时, headBars 视为空,
  // 避免先渲染旧周期 bar 造成 K 线"先一堆旧的再换新"闪烁。
  const headBars = (olderKeyRef.current === key && !head.isLoading && head.data?.bars)
    ? head.data.bars
    : EMPTY
  const bars = useMemo(() => mergeBarsAsc(safeOlder, headBars), [safeOlder, headBars])

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
