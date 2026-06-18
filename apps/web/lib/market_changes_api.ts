// 盘面异动相关接口客户端 (个股异动 / 板块异动 / 涨停池 / 竞价 / 30min 结论轮)。
// 后端 SSoT: apps/api/routes/market_changes.py + conclusions.py。

export interface Meta {
  stale: boolean
  reason?: string | null
  fresh_at?: string | null
  trade_date?: string | null
}

export interface ChangeRow {
  time: string
  symbol: string
  name?: string | null
  change_type: string
  category?: string | null
  price?: number | null
  change_pct?: number | null
  collected: boolean
}

export interface ChangesResponse {
  market: string
  changes: ChangeRow[]
  counts: Record<string, number>
  meta: Meta
}

export interface BoardRow {
  board_name: string
  change_pct?: number | null
  main_net_inflow?: number | null
  change_total?: number | null
  top_symbol?: string | null
  top_name?: string | null
  top_direction?: string | null
}

export interface BoardChangesResponse {
  market: string
  boards: BoardRow[]
  meta: Meta
}

export interface LimitRow {
  symbol: string
  name?: string | null
  change_pct?: number | null
  price?: number | null
  limit_price?: number | null
  amount?: number | null
  turnover_rate?: number | null
  seal_amount?: number | null
  first_seal_time?: string | null
  last_seal_time?: string | null
  break_count?: number | null
  ladder_count?: number | null
  industry?: string | null
  collected: boolean
}

export interface LimitPoolResponse {
  market: string
  pool_type: string
  items: LimitRow[]
  meta: Meta
}

export interface AuctionResponse {
  market: string
  trade_date: string
  auction_up: ChangeRow[]
  auction_down: ChangeRow[]
  one_word_limit: LimitRow[]
  prev_limit_today: LimitRow[]
  late_surge: ChangeRow[]
  late_plunge: ChangeRow[]
  late_seal: ChangeRow[]
  late_broken: ChangeRow[]
  meta: Meta
}

export interface ConclusionSection {
  key: string
  title: string
  label: string
  score: number
  summary: string
  evidence: Record<string, unknown>
}

export interface RoundSlot {
  slot_time: string
  phase: 'cooled' | 'active'
  window_minutes: number
  sections: ConclusionSection[]
  data_gaps: string[]
  generated_at?: string | null
}

export interface IntradayRoundsResponse {
  market: string
  trade_date: string
  slots: RoundSlot[]
  meta: Record<string, unknown>
}

async function j<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${path} -> ${res.status}`)
  return res.json() as Promise<T>
}

export const fetchChanges = (market: string, categories?: string[]) =>
  j<ChangesResponse>(
    `/api/markets/${market}/changes${categories?.length ? `?types=${categories.join(',')}` : ''}`,
  )

export const fetchBoardChanges = (market: string) =>
  j<BoardChangesResponse>(`/api/markets/${market}/board-changes`)

export const fetchLimitPool = (market: string, poolType: string) =>
  j<LimitPoolResponse>(`/api/markets/${market}/limit-pool?pool_type=${poolType}`)

export const fetchAuction = (market: string) =>
  j<AuctionResponse>(`/api/markets/${market}/auction`)

export const fetchIntradayRounds = (market: string) =>
  j<IntradayRoundsResponse>(`/api/conclusions/intraday-rounds?market=${market}`)
