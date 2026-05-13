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

export type Interval = '1d' | '1wk' | '1mo' | '1m' | '5m' | '15m' | '30m' | '60m'

export interface BarDTO {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface BarsResponse {
  symbol: string
  interval: Interval
  bars: BarDTO[]
}

export interface FundFlowRow {
  ts: string
  main_net: number | null
  super_large_net: number | null
  large_net: number | null
  medium_net: number | null
  small_net: number | null
}

export interface FundFlowResponse {
  symbol: string
  rows: FundFlowRow[]
}

export interface NorthFlowRow {
  ts: string
  hgt_net: number | null
  sgt_net: number | null
}

export interface SectorInfo {
  name: string
  classification: string
  updated_at: string
}

export interface Watchlist {
  id: number
  name: string
  is_archived: boolean
  created_at: string
}
