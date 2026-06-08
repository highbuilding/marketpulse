import type {
  BarsResponse, ChipSummaryResponse, FundFlowResponse,
  Interval, SearchHit, SymbolProfile, VolumeIndicatorsResponse,
} from './types'

export async function fetchBars(symbol: string, interval: Interval, days: number): Promise<BarsResponse> {
  const r = await fetch(`/api/symbols/${symbol}/bars?interval=${interval}&days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

// 游标分页历史 (币安/TradingView 反向翻页口径)。
// before 空 = 最新一页; 传上一页最老一根的 ts 翻更早一页, 直到返回 < limit (到顶)。
export async function fetchBarsHistory(
  symbol: string, interval: Interval, opts: { before?: string; limit?: number } = {},
): Promise<BarsResponse> {
  const sp = new URLSearchParams({ interval, limit: String(opts.limit ?? 500) })
  if (opts.before) sp.set('before', opts.before)
  const r = await fetch(`/api/symbols/${symbol}/bars/history?${sp}`, { cache: 'no-store' })
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

export async function searchSymbols(
  q: string, limit = 20, market?: string,
): Promise<{ query: string; hits: SearchHit[] }> {
  const sp = new URLSearchParams({ q, limit: String(limit) })
  if (market) sp.set('market', market)
  const r = await fetch(`/api/symbols/search?${sp}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
