'use client'
// 概览页与自选页共享"当前选中的清单"。自选页切清单 → 概览跟随。
// localStorage 持久化 + 跨组件同步(storage 事件 + 自定义事件)。
import { useState, useEffect, useCallback } from 'react'

const KEY = 'mp:activeWatchlistId'
const EVT = 'mp:activeWatchlistId:changed'

export function useActiveWatchlistId(): [number | null, (id: number) => void] {
  const [id, setId] = useState<number | null>(null)

  useEffect(() => {
    const read = () => {
      const v = typeof window !== 'undefined' ? window.localStorage.getItem(KEY) : null
      setId(v != null ? Number(v) : null)
    }
    read()
    // 跨标签页(storage)+ 同页跨组件(自定义事件)同步
    window.addEventListener('storage', read)
    window.addEventListener(EVT, read)
    return () => {
      window.removeEventListener('storage', read)
      window.removeEventListener(EVT, read)
    }
  }, [])

  const select = useCallback((next: number) => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(KEY, String(next))
      window.dispatchEvent(new Event(EVT))
    }
    setId(next)
  }, [])

  return [id, select]
}
