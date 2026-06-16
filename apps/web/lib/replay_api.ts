import type { LiveMessageCategory, Market, ReplayResponse } from './types'

export interface ReplayFetchOptions {
  msgLimit?: number
  msgOffset?: number
  category?: LiveMessageCategory
}

// 盘后回放: 按 BJT 自然日聚合当日实盘消息 + 题材快照序列。
// date 缺省由后端取今天(BJT)。
export async function fetchReplay(
  market: Market,
  date?: string,
  options: ReplayFetchOptions = {},
): Promise<ReplayResponse> {
  const params = new URLSearchParams({ market })
  if (date) params.set('date', date)
  if (options.category) params.set('category', options.category)
  if (options.msgLimit != null) params.set('msg_limit', String(options.msgLimit))
  if (options.msgOffset != null) params.set('msg_offset', String(options.msgOffset))
  const res = await fetch(`/api/replay?${params.toString()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/replay -> ${res.status}`)
  return res.json()
}
