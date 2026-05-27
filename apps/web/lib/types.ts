import type { Market } from './markets'
export type { Market }

export interface AdapterHealth {
  state: 'ok' | 'degraded' | 'disabled' | 'down'
  detail: string | null
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'down'
  markets_enabled: Market[]
  adapters: Record<Market, AdapterHealth>
}

export type Interval = '1d' | '1wk' | '1mo' | '1m' | '5m' | '15m' | '30m' | '60m' | '4h'

export interface BarDTO {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount?: number | null
  turnover?: number | null
  outstanding_share?: number | null
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

export interface ChipSummaryRow {
  trade_date: string
  profit_ratio: number | null
  avg_cost: number | null
  cost_90_low: number | null
  cost_90_high: number | null
  concentration_90: number | null
  cost_70_low: number | null
  cost_70_high: number | null
  concentration_70: number | null
}

export interface ChipSummaryResponse {
  symbol: string
  rows: ChipSummaryRow[]
}

export interface VolumeIndicatorRow {
  ts: string
  volume: number
  amount: number | null
  turnover: number | null
  vol_ma5: number | null
  vol_ma20: number | null
  amount_ma20: number | null
  volume_ratio: number | null
  single_bar_volume_ratio: number | null
  obv: number
  is_volume_breakout: boolean
  is_shrink_pullback: boolean
}

export interface VolumeIndicatorsResponse {
  symbol: string
  interval: Interval
  rows: VolumeIndicatorRow[]
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

export interface SymbolProfile {
  symbol: string
  name: string | null
  market: Market | null
}

export interface SymbolQuote {
  symbol: string
  price: number | null
  change_pct: number | null
  volume: number | null
  ts: string | null
}

export interface SearchHit {
  symbol: string
  name: string
  market: Market
}

export type AnySignalInterval = '15m' | '30m' | '60m' | '4h' | '1d'
export type DetailSignalInterval = '15m' | '30m' | '60m' | '4h' | '1d'

export interface CDSignalDTO {
  id: number
  symbol: string
  interval: AnySignalInterval
  signal_type: 'buy' | 'sell'
  bar_ts: string
  detected_at: string
  price: number
  d_value: number | null
  acknowledged: boolean
}

export interface IndexMinutePoint {
  ts: string
  close: number
  volume: number
}

export interface IndexMinuteResponse {
  symbol: string
  name: string
  granularity: '1m' | '5m' | '1d'
  points: IndexMinutePoint[]
}

export interface AIMarketBreadth {
  total: number
  advancers: number
  decliners: number
  flat: number
  up_limit: number
  down_limit: number
  total_amount: number
  up_ratio: number
  down_ratio: number
  net_width: number
}

export interface AIMarketSymbol {
  symbol: string
  name: string | null
  price: number | null
  change_pct: number | null
  volume: number | null
  sectors: string[]
}

export interface AIMarketRank {
  symbol: string
  name: string
  price: number
  change_pct: number
  volume: number
  amount: number
}

export interface AIMarketSector {
  code: string
  name: string
  change_pct: number
  company_count: number
  leader_name: string
  leader_change_pct: number
  leader_symbol: string | null
  main_net: number | null
  constituents: AIMarketSymbol[] | null
  up_count: number | null
  down_count: number | null
  up_ratio: number | null
  avg_change_pct: number | null
  leader_dominance_pct: number | null
  breadth_label: string
}

export interface AIMarketEvent {
  level: string
  category: string
  title: string
  detail: string
  symbols: string[]
  score: number
}

export interface AIMarketIndexStrength {
  ranking: Array<{ symbol: string; name: string | null; change_pct: number | null }>
  small_vs_large_pct: number | null
  growth_vs_large_pct: number | null
}

export interface AIMarketPacket {
  generated_at: string
  market: 'ashare'
  indices: AIMarketSymbol[]
  breadth: AIMarketBreadth
  top_gainers: AIMarketRank[]
  top_losers: AIMarketRank[]
  hot_sectors: AIMarketSector[]
  weak_sectors: AIMarketSector[]
  watchlist: AIMarketSymbol[]
  index_strength: AIMarketIndexStrength
  events: AIMarketEvent[]
  ai_brief: Record<string, unknown>
  degraded: string[]
}
