'use client'

// 分时折线数据 hook。
// - REST: SWR 拉 /api/symbols/{symbol}/intraday-line 首屏当日全量点。
// - SSE:  EventSource 订阅 /api/sse/intraday/{symbol},init/point 事件实时追加/覆盖。
// - 同 ts 覆盖,新 ts 追加,保持升序。
// - dev 环境直连 8787 绕开 Next.js dev rewrites buffer 问题(同 use_kline_stream 约定)。

import { useEffect, useRef, useState } from 'react'
import useSWR from 'swr'

export interface IntradayPoint {
  ts: string
  price: number
  avg_price: number
  cum_amount: number
  cum_volume: number
}

interface IntradayLineResponse {
  symbol: string
  points: IntradayPoint[]
  meta: {
    stale: boolean
    reason?: string
  }
}

async function fetchIntradayLine(symbol: string): Promise<IntradayLineResponse> {
  const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/intraday-line`)
  if (!res.ok) throw new Error(`intraday-line fetch failed: ${res.status}`)
  return res.json() as Promise<IntradayLineResponse>
}

// 按 ts 升序合并两组点,后者在 ts 冲突时获胜(SSE 覆盖 REST)。
function mergePoints(base: IntradayPoint[], incoming: IntradayPoint[]): IntradayPoint[] {
  const map = new Map<string, IntradayPoint>()
  for (const p of base) map.set(p.ts, p)
  for (const p of incoming) map.set(p.ts, p)
  return Array.from(map.values()).sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))
}

export interface IntradayLineResult {
  points: IntradayPoint[]
  stale: boolean
}

export function useIntradayLine(symbol: string, enabled: boolean): IntradayLineResult {
  const { data: restData } = useSWR(
    enabled ? `intraday-line:${symbol}` : null,
    () => fetchIntradayLine(symbol),
    { revalidateOnFocus: false },
  )

  // SSE 追加/覆盖的增量点(相对于 REST 基线)
  const [ssePoints, setSsePoints] = useState<IntradayPoint[]>([])
  // 当前 symbol 的 ref,用于 SSE 事件处理器内判断是否已切换
  const symbolRef = useRef(symbol)
  symbolRef.current = symbol

  useEffect(() => {
    if (!enabled) {
      setSsePoints([])
      return
    }
    // 切 symbol 时清空旧 SSE 增量
    setSsePoints([])

    const apiBase =
      typeof window !== 'undefined' && window.location.port === '3000'
        ? 'http://127.0.0.1:8787'
        : ''
    const url = `${apiBase}/api/sse/intraday/${encodeURIComponent(symbol)}`
    const es = new EventSource(url)

    if (typeof window !== 'undefined') {
      console.log('[useIntradayLine] connect', { symbol, url })
    }

    // init 事件:服务端推送当前最新一个点(实时尾部)
    es.addEventListener('init', (msg) => {
      const data = JSON.parse((msg as MessageEvent).data) as { point: IntradayPoint; symbol: string }
      if (data.symbol !== symbolRef.current) return
      const p = data.point
      setSsePoints((prev) => mergePoints(prev, [p]))
    })

    // point 事件:分时点更新或新增
    es.addEventListener('point', (msg) => {
      const data = JSON.parse((msg as MessageEvent).data) as {
        market: string
        symbol: string
        ts: string
        price: number
        avg_price: number
        cum_amount?: number
        cum_volume?: number
      }
      if (data.symbol !== symbolRef.current) return
      const p: IntradayPoint = {
        ts: data.ts,
        price: data.price,
        avg_price: data.avg_price,
        cum_amount: data.cum_amount ?? 0,
        cum_volume: data.cum_volume ?? 0,
      }
      setSsePoints((prev) => mergePoints(prev, [p]))
    })

    // ping 事件:心跳,忽略
    es.addEventListener('ping', () => { /* keepalive */ })

    es.onopen = () => {
      console.log('[useIntradayLine] open', { symbol })
    }
    es.onerror = (e) => {
      // 留空让 EventSource 自动重连;仅 warn 便于排查
      console.warn('[useIntradayLine] error', e, 'readyState=', es.readyState)
    }

    return () => {
      console.log('[useIntradayLine] close', { symbol })
      es.close()
    }
  }, [symbol, enabled])

  const restPoints = restData?.points ?? []
  const stale = restData?.meta?.stale ?? false

  // REST 基线 + SSE 增量合并,SSE 在 ts 冲突时获胜
  const points = mergePoints(restPoints, ssePoints)

  return { points, stale }
}
