import type { Market, ReplayResponse } from './types'

// 盘后回放: 按 BJT 自然日聚合当日实盘消息 + 题材快照序列。
// date 缺省由后端取今天(BJT)。
export async function fetchReplay(market: Market, date?: string): Promise<ReplayResponse> {
  const params = new URLSearchParams({ market })
  if (date) params.set('date', date)
  const res = await fetch(`/api/replay?${params.toString()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/replay -> ${res.status}`)
  return res.json()
}
