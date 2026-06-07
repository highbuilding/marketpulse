import { apiFetch } from './api_fetch'
import type { Market, Position } from './types'

export interface PositionInput {
  market: Market
  symbol: string
  name?: string | null
  quantity?: number
  cost_price?: number | null
  opened_at?: string | null
  strategy_tag?: string | null
  entry_reason?: string | null
  status?: string
  note?: string | null
}

export async function listPositions(
  market: Market,
  includeClosed = false,
): Promise<{ positions: Position[] }> {
  const params = new URLSearchParams({ market, include_closed: String(includeClosed) })
  const r = await apiFetch(`/api/positions?${params.toString()}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function upsertPosition(input: PositionInput): Promise<{ id: number }> {
  const r = await apiFetch('/api/positions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function patchPosition(
  market: Market,
  symbol: string,
  patch: Partial<Omit<PositionInput, 'market' | 'symbol'>>,
): Promise<{ id: number }> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/positions/${encodeURIComponent(symbol)}?${params.toString()}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function closePosition(market: Market, symbol: string): Promise<void> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/positions/${encodeURIComponent(symbol)}?${params.toString()}`, {
    method: 'DELETE',
  })
  if (!r.ok) throw new Error(`${r.status}`)
}
