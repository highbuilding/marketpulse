import type {
  BarsResponse, ChipSummaryResponse, FundFlowResponse, IndexMinuteResponse,
  Interval, SearchHit, SymbolProfile, SymbolQuote, VolumeIndicatorsResponse,
} from './types'

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

export async function fetchChipSummary(symbol: string, days = 90): Promise<ChipSummaryResponse> {
  const r = await fetch(`/api/symbols/${symbol}/chip_summary?days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchVolumeIndicators(
  symbol: string, interval: Interval, days = 120,
): Promise<VolumeIndicatorsResponse> {
  const r = await fetch(`/api/symbols/${symbol}/volume_indicators?interval=${interval}&days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchSymbolProfile(symbol: string): Promise<SymbolProfile> {
  const r = await fetch(`/api/symbols/${symbol}/profile`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchSymbolProfiles(symbols: string[]): Promise<SymbolProfile[]> {
  if (symbols.length === 0) return []
  const q = encodeURIComponent(symbols.join(','))
  const r = await fetch(`/api/symbols/profiles?symbols=${q}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  const data: { profiles: SymbolProfile[] } = await r.json()
  return data.profiles
}

export async function fetchSymbolQuote(symbol: string): Promise<SymbolQuote> {
  const r = await fetch(`/api/symbols/${symbol}/quote`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function searchSymbols(
  q: string, limit = 20, market?: string,
): Promise<{ query: string; hits: SearchHit[] }> {
  const sp = new URLSearchParams({ q, limit: String(limit) })
  if (market) sp.set('market', market)
  const r = await fetch(`/api/symbols/search?${sp}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchIndexMinute(symbol: string, days = 1): Promise<IndexMinuteResponse> {
  const r = await fetch(`/api/indices/${encodeURIComponent(symbol)}/minute?days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
