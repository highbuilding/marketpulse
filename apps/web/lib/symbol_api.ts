import type { BarsResponse, FundFlowResponse, Interval } from './types'

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
