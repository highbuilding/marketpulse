// 通过 SSE 实时订阅 K 线 (crypto 详情页用)。
// 后端路由: /api/sse/bars/{symbol}/{interval} 推送 init / bar / tick 三种 event。
// - init: 一次性下发历史 bars (全量替换)
// - bar (final=true): 一根 bar 收盘 → 替换末根 (ts 匹配) 或 append
// - tick (final=false): 进行中的 bar 每 1-2s 更新一次 → 替换末根 (ts 匹配) 或 append
// onerror 留空 → EventSource 自动重连。
//
// A 股 / 美股仍走 SWR (传 enabled=false 即可禁用本 hook),P7 后续扩展。

import { useEffect, useState } from 'react'
import type { BarDTO } from '@/lib/types'

export interface KlineEvent {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  final: boolean
}

export function useKlineStream(
  symbol: string,
  interval: string,
  enabled: boolean,
): BarDTO[] {
  const [bars, setBars] = useState<BarDTO[]>([])

  useEffect(() => {
    if (!enabled) {
      setBars([])
      return
    }
    const url = `/api/sse/bars/${encodeURIComponent(symbol)}/${interval}`
    const es = new EventSource(url)

    const toBarDTO = (e: KlineEvent): BarDTO => ({
      ts: e.ts,
      open: e.open,
      high: e.high,
      low: e.low,
      close: e.close,
      volume: e.volume,
    })

    es.addEventListener('init', (msg) => {
      const data = JSON.parse((msg as MessageEvent).data)
      setBars((data.bars as KlineEvent[]).map(toBarDTO))
    })

    es.addEventListener('bar', (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as KlineEvent
      setBars((prev) => {
        const last = prev[prev.length - 1]
        const dto = toBarDTO(ev)
        if (last && last.ts === ev.ts) {
          return [...prev.slice(0, -1), dto]
        }
        return [...prev, dto]
      })
    })

    es.addEventListener('tick', (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as KlineEvent
      setBars((prev) => {
        const last = prev[prev.length - 1]
        const dto = toBarDTO(ev)
        if (last && last.ts === ev.ts) {
          return [...prev.slice(0, -1), dto]
        }
        return [...prev, dto]
      })
    })

    es.onerror = () => {
      /* EventSource 自动重连 — 不做处理 */
    }

    return () => {
      es.close()
    }
  }, [symbol, interval, enabled])

  return bars
}
