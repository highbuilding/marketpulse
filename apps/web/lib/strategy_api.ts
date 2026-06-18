import type {
  Market,
  StrategyListResponse,
  StrategyReportResponse,
  TradeInstructionsResponse,
} from './types'

async function j<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${path} -> ${res.status}`)
  return res.json() as Promise<T>
}

export function fetchStrategyBacktests(market: Market): Promise<StrategyListResponse> {
  return j<StrategyListResponse>(`/api/strategy/backtests?market=${market}`)
}

export function fetchStrategyReport(
  market: Market,
  strategyId: string,
): Promise<StrategyReportResponse> {
  return j<StrategyReportResponse>(
    `/api/strategy/backtests/${encodeURIComponent(strategyId)}?market=${market}`,
  )
}

export function fetchTradeInstructions(market: Market): Promise<TradeInstructionsResponse> {
  return j<TradeInstructionsResponse>(`/api/strategy/instructions?market=${market}`)
}
