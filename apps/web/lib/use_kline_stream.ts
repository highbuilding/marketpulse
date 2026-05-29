// 通过 SSE 实时订阅 K 线 (crypto 详情页用)。
// 后端路由: /api/sse/bars/{symbol}/{interval} 推送 init / bar / tick 三种 event。
// - init: 只下发当前进行中 bar (历史由 REST /bars/history 分页负责, 两通道解耦)
// - bar (final=true): 一根 bar 收盘 → 替换末根 (ts 匹配) 或 append
// - tick (final=false): 进行中的 bar 每 1-2s 更新一次 → 替换末根 (ts 匹配) 或 append
// onerror 留空 → EventSource 自动重连。
// 返回值是"实时尾部"(最右一根附近), 调用方 merge 到 REST 历史之上。
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
    // SSE 直连 api 端口, 绕开 Next.js dev rewrites 代理 (代理会 buffer chunked stream
    // 导致浏览器 EventSource 即便 readyState=OPEN 也收不到 init/tick 帧).
    // 生产环境若同源部署可直接相对路径; 本地 dev 显式指定 8787.
    const apiBase =
      typeof window !== 'undefined' && window.location.port === '3000'
        ? 'http://127.0.0.1:8787'
        : ''
    const url = `${apiBase}/api/sse/bars/${encodeURIComponent(symbol)}/${interval}`
    const es = new EventSource(url)

    if (typeof window !== 'undefined') {
      console.log('[useKlineStream] connect', { symbol, interval, url })
    }

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
      const dtos = (data.bars as KlineEvent[]).map(toBarDTO)
      // 按 ts 去重 (后到覆盖) + 升序排序, 防御 server 端 init 偶发重复 ts
      const map = new Map<string, BarDTO>()
      for (const b of dtos) map.set(b.ts, b)
      const sorted = Array.from(map.values()).sort((a, b) => a.ts.localeCompare(b.ts))
      console.log('[useKlineStream] init', { count: sorted.length, last: sorted[sorted.length - 1]?.ts })
      setBars(sorted)
    })

    es.addEventListener('bar', (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as KlineEvent
      setBars((prev) => {
        const last = prev[prev.length - 1]
        const dto = toBarDTO(ev)
        if (last && last.ts === ev.ts) {
          return [...prev.slice(0, -1), dto]
        }
        // 防御: 即便 ts > last 也确认 strict ascending (旧 stream 残留 race)
        if (last && ev.ts <= last.ts) {
          return prev  // 丢弃乱序事件
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
        if (last && ev.ts <= last.ts) {
          return prev  // 丢弃乱序 tick
        }
        return [...prev, dto]
      })
    })

    es.onopen = () => {
      console.log('[useKlineStream] open', { symbol, interval })
    }
    es.onerror = (e) => {
      console.warn('[useKlineStream] error', e, 'readyState=', es.readyState)
    }

    return () => {
      console.log('[useKlineStream] close', { symbol, interval })
      es.close()
    }
  }, [symbol, interval, enabled])

  return bars
}
