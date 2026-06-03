'use client'
// 订阅 /api/sse/signals,实时拿新 CD 信号。全量推,调用方按市场过滤。
import { useEffect, useState } from 'react'
import type { CDSignalDTO } from './types'

export function useSignalStream(): CDSignalDTO[] {
  const [live, setLive] = useState<CDSignalDTO[]>([])
  useEffect(() => {
    const apiBase =
      typeof window !== 'undefined' && window.location.port === '3000'
        ? 'http://127.0.0.1:8787'
        : ''
    const es = new EventSource(`${apiBase}/api/sse/signals`)
    es.addEventListener('signal', (e: MessageEvent) => {
      try {
        const s = JSON.parse(e.data) as CDSignalDTO
        setLive((cur) => [s, ...cur].slice(0, 100))
      } catch {
        /* 忽略坏帧 */
      }
    })
    es.onerror = () => {
      /* 留空让 EventSource 自动重连 */
    }
    return () => es.close()
  }, [])
  return live
}
