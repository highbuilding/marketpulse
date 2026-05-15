import type { CDSignalDTO } from './types'

export async function listCDSignals(params?: {
  intervals?: string[]
  symbol?: string
  onlyUnack?: boolean
  limit?: number
}): Promise<{ signals: CDSignalDTO[] }> {
  const sp = new URLSearchParams()
  if (params?.intervals) for (const iv of params.intervals) sp.append('intervals', iv)
  if (params?.symbol) sp.set('symbol', params.symbol)
  if (params?.onlyUnack) sp.set('only_unack', 'true')
  if (params?.limit) sp.set('limit', String(params.limit))
  const r = await fetch(`/api/cd-signals?${sp}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function listCDSignalsBySymbol(
  symbol: string,
  intervals?: string[],
): Promise<{ signals: CDSignalDTO[] }> {
  const sp = new URLSearchParams()
  if (intervals) for (const iv of intervals) sp.append('intervals', iv)
  const r = await fetch(
    `/api/cd-signals/by-symbol/${encodeURIComponent(symbol)}?${sp}`,
    { cache: 'no-store' },
  )
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchWatchlistEvents(
  interval: string, limit = 100,
): Promise<{ signals: CDSignalDTO[] }> {
  const sp = new URLSearchParams({ interval, limit: String(limit) })
  const r = await fetch(`/api/cd-signals/watchlist-events?${sp}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function ackCDSignal(id: number): Promise<void> {
  const r = await fetch(`/api/cd-signals/${id}/ack`, { method: 'POST' })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function scanCDSignals(body?: {
  symbols?: string[]
  intervals?: string[]
}): Promise<{ new_signals: number; interval_breakdown: Record<string, number> }> {
  const r = await fetch('/api/cd-signals/scan', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function unackCount(): Promise<{ count: number }> {
  const r = await fetch('/api/cd-signals/unack-count', { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
