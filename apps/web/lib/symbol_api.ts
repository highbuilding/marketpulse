import type { BarsResponse, FundFlowResponse, IndexMinuteResponse, Interval, SearchHit, SymbolProfile } from './types'

export async function fetchBars(symbol: string, interval: Interval, days: number): Promise<BarsResponse> {
  const r = await fetch(`/api/symbols/${symbol}/bars?interval=${interval}&days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchSymbolFundFlow(symbol: string, days = 30): Promise<FundFlowResponse> {
  const r = await fetch(`/api/symbols/${symbol}/fund_flow?days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchSymbolProfile(symbol: string): Promise<SymbolProfile> {
  const r = await fetch(`/api/symbols/${symbol}/profile`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function searchSymbols(q: string, limit = 20): Promise<{ query: string; hits: SearchHit[] }> {
  const r = await fetch(`/api/symbols/search?q=${encodeURIComponent(q)}&limit=${limit}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchIndexMinute(symbol: string, days = 1): Promise<IndexMinuteResponse> {
  const r = await fetch(`/api/indices/${encodeURIComponent(symbol)}/minute?days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
