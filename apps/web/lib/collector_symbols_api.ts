import { apiFetch } from './api_fetch'
import type {
  CollectorSymbolMutationResponse,
  CollectorSymbolsResponse,
  CollectorSymbolsStatusResponse,
  Market,
} from './types'

export interface CollectorSymbolInput {
  market: Market
  symbol: string
  name?: string | null
  enabled?: boolean
  collect_snapshot?: boolean
  collect_5m?: boolean
  collect_signals?: boolean
  note?: string | null
}

export async function listCollectorSymbols(
  market: Market,
  includeDisabled = true,
): Promise<CollectorSymbolsResponse> {
  const params = new URLSearchParams({ market, include_disabled: String(includeDisabled) })
  const r = await apiFetch(`/api/collector-symbols?${params.toString()}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function listCollectorSymbolStatus(
  market: Market,
  includeDisabled = true,
): Promise<CollectorSymbolsStatusResponse> {
  const params = new URLSearchParams({ market, include_disabled: String(includeDisabled) })
  const r = await apiFetch(`/api/collector-symbols/status?${params.toString()}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function upsertCollectorSymbol(
  input: CollectorSymbolInput,
): Promise<CollectorSymbolMutationResponse> {
  const r = await apiFetch('/api/collector-symbols', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function deleteCollectorSymbol(
  market: Market,
  symbol: string,
): Promise<CollectorSymbolMutationResponse> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/collector-symbols/${encodeURIComponent(symbol)}?${params.toString()}`, {
    method: 'DELETE',
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
