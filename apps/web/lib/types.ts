export type Market = 'ashare' | 'hk' | 'us' | 'crypto'

export interface QuoteDTO {
  symbol: string
  price: number
  change_pct: number
  volume: number
  source: string
  ts: string
}

export interface OverviewResponse {
  market: Market
  status: 'ok' | 'warming' | 'degraded'
  quotes: QuoteDTO[]
  top_gainers: QuoteDTO[]
  top_losers: QuoteDTO[]
  indices: QuoteDTO[]
}

export interface AdapterHealth {
  state: 'ok' | 'degraded' | 'disabled' | 'down'
  detail: string | null
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'down'
  markets_enabled: Market[]
  adapters: Record<Market, AdapterHealth>
}
