'use client'

import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'

import { fetchLiveMessages } from './live_messages_api'
import type { LiveMessage, Market } from './types'

export function useLiveMessages(market: Market, limit = 50) {
  const [streamed, setStreamed] = useState<LiveMessage[]>([])
  const { data, error, isLoading, mutate } = useSWR(
    ['live-messages', market, limit],
    () => fetchLiveMessages(market, limit),
    { refreshInterval: 60_000 },
  )

  useEffect(() => {
    setStreamed([])
    const es = new EventSource(`/api/sse/live-messages?market=${encodeURIComponent(market)}`)
    const onMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data) as LiveMessage
        setStreamed((prev) => [msg, ...prev.filter((m) => m.id !== msg.id)].slice(0, limit))
      } catch {
        // ignore malformed frames; periodic SWR refresh is the fallback.
      }
    }
    es.addEventListener('live-message', onMessage)
    es.onerror = () => {
      void mutate()
    }
    return () => {
      es.removeEventListener('live-message', onMessage)
      es.close()
    }
  }, [market, limit, mutate])

  const messages = useMemo(() => {
    const base = data?.messages ?? []
    const seen = new Set<string>()
    return [...streamed, ...base]
      .filter((m) => {
        if (seen.has(m.id)) return false
        seen.add(m.id)
        return true
      })
      .sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))
      .slice(0, limit)
  }, [data?.messages, streamed, limit])

  return { messages, error, isLoading, refresh: mutate }
}

