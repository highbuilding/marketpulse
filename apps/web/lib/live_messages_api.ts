import type { LiveMessage, LiveMessageStatus, Market } from './types'

export async function fetchLiveMessages(
  market: Market,
  limit = 50,
): Promise<{ messages: LiveMessage[] }> {
  const params = new URLSearchParams({ market, limit: String(limit) })
  const res = await fetch(`/api/live-messages?${params.toString()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/live-messages -> ${res.status}`)
  return res.json()
}

export async function fetchLiveMessageAiContext(
  market: Market,
  minutes = 30,
): Promise<{ messages: LiveMessage[] }> {
  const params = new URLSearchParams({ market, minutes: String(minutes), limit: '100' })
  const res = await fetch(`/api/live-messages/ai-context?${params.toString()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/live-messages/ai-context -> ${res.status}`)
  return res.json()
}

export async function fetchLiveMessageStatus(market: Market): Promise<LiveMessageStatus> {
  const params = new URLSearchParams({ market })
  const res = await fetch(`/api/live-messages/status?${params.toString()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/live-messages/status -> ${res.status}`)
  return res.json()
}
