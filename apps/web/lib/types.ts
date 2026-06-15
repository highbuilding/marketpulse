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
  meta?: ResponseMeta
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

export interface Position {
  id: number
  market: Market
  symbol: string
  name: string | null
  quantity: number
  cost_price: number | null
  close_price: number | null
  profit_amount: number | null
  profit_pct: number | null
  opened_at: string | null
  closed_at: string | null
  strategy_tag: string | null
  entry_reason: string | null
  status: string
  note: string | null
  created_at: string | null
  updated_at: string | null
}

export type ThemePriority = 'P0' | 'P1' | 'P2' | 'P3'
export type ThemeClassification = 'index_weight' | 'industry' | 'concept' | 'theme' | 'watch'

export interface ThemeDefinition {
  market: Market
  theme_code: string
  theme_name: string
  classification: ThemeClassification
  priority: ThemePriority
  enabled: boolean
  source: string
  seed_version: string | null
  note: string | null
  member_count: number
  created_at: string | null
  updated_at: string | null
}

export interface ThemeConstituent {
  market: Market
  theme_code: string
  symbol: string
  name: string | null
  role_hint: string | null
  weight: number | null
  enabled: boolean
  source: string
  seed_version: string | null
  note: string | null
  created_at: string | null
  updated_at: string | null
}

export interface CollectorSymbol {
  market: Market
  symbol: string
  name: string | null
  enabled: boolean
  source: string
  collect_snapshot: boolean
  collect_5m: boolean
  collect_signals: boolean
  note: string | null
  created_at: string | null
  updated_at: string | null
}

export type CollectorSymbolHealth = 'ok' | 'warming' | 'stale' | 'disabled'

export interface CollectorSymbolStatus extends CollectorSymbol {
  snapshot_ts: string | null
  snapshot_age_seconds: number | null
  kline_5m_ts: string | null
  kline_5m_age_seconds: number | null
  health: CollectorSymbolHealth
  health_reason: string
}

export interface CollectorSymbolsResponse {
  symbols: CollectorSymbol[]
  total: number
  enabled: number
  snapshot_count: number
  kline_5m_count: number
  signals_count: number
}

export interface CollectorSymbolsStatusResponse {
  symbols: CollectorSymbolStatus[]
  total: number
  enabled: number
  ok: number
  warming: number
  stale: number
  disabled: number
  snapshot_count: number
  kline_5m_count: number
  signals_count: number
}

export interface CollectorSymbolMutationResponse {
  symbol: string
  action: string
  collector_confirmed: boolean
  collector_message: string | null
  refill_queued: boolean
}

export interface SymbolProfile {
  symbol: string
  name: string | null
  market: Market | null
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

/**
 * 响应元数据 — 后端 Plan 3 之后所有路由都可能带这个字段。
 * stale: 数据陈旧(collector 没及时刷新)
 * partial: 数据存在但部分字段缺失(如 A 股 daily 缺 amount/turnover)
 * reason: 文字说明(warming_up / hk_index_collector_pending 等)
 * data_age_seconds: 数据陈旧多久
 * fresh_at: 数据最近一次更新时间(ISO)
 * missing_sections: dashboard 路由缺失了哪些 section
 */
export interface ResponseMeta {
  stale?: boolean
  partial?: boolean
  reason?: string
  data_age_seconds?: number
  fresh_at?: string
  missing_sections?: string[]
}

export type LiveMessageLevel = 'info' | 'watch' | 'warning' | 'critical'
export type LiveMessageCategory = 'index' | 'theme' | 'watchlist' | 'signal' | 'risk' | 'system'

export interface LiveMessage {
  id: string
  market: Market
  ts: string
  level: LiveMessageLevel
  category: LiveMessageCategory
  title: string
  body: string
  theme_code: string | null
  symbol: string | null
  symbols: string[]
  source_event: string
  source_event_id: string | null
  dedupe_key: string
  payload: Record<string, unknown>
  rule_version: string
  created_at: string | null
}

export interface LiveMessageStatus {
  market: Market
  enabled_themes: number
  enabled_constituents: number
  watchlist_symbols: number
  latest_message_ts: string | null
  latest_message_title: string | null
  rule_version: string
}
