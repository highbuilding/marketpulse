import { apiFetch } from './api_fetch'
import type { Market, Position } from './types'

export interface PositionInput {
  market: Market
  symbol: string
  name?: string | null
  quantity?: number
  cost_price?: number | null
  opened_at?: string | null
  strategy_tag?: string | null
  entry_reason?: string | null
  note?: string | null
}

export async function listPositions(
  market: Market,
  includeClosed = false,
): Promise<{ positions: Position[] }> {
  const params = new URLSearchParams({ market, include_closed: String(includeClosed) })
  const r = await apiFetch(`/api/positions?${params.toString()}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

// 新建持仓(A 方案: 同标的可多条)
export async function createPosition(input: PositionInput): Promise<{ id: number }> {
  const r = await apiFetch('/api/positions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

// 按 id 编辑
export async function patchPosition(
  id: number,
  patch: Partial<Omit<PositionInput, 'market' | 'symbol'>>,
): Promise<{ id: number }> {
  const r = await apiFetch(`/api/positions/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

// 按 id 平仓: 手填平仓价, 盈亏由后端算 ((平仓价-开仓价)*股数) 并存库
export async function closePosition(
  id: number,
  closePrice?: number | null,
  closedAt?: string | null,
): Promise<{ id: number }> {
  const r = await apiFetch(`/api/positions/${id}/close`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ close_price: closePrice ?? null, closed_at: closedAt ?? null }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

// 按 id 删除(硬删除, 区别于平仓软删除)
export async function deletePosition(id: number): Promise<void> {
  const r = await apiFetch(`/api/positions/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}
