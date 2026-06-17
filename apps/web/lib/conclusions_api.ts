import type { DailyReviewResponse, Market } from './types'

// 每日复盘: 优先返回 collector 已生成存档 (日线走势/板块位置/龙头分层 + 消息层)。
// date 缺省由后端取今天 (BJT)。
export async function fetchDailyReview(
  market: Market,
  date?: string,
): Promise<DailyReviewResponse> {
  const params = new URLSearchParams({ market })
  if (date) params.set('date', date)
  const res = await fetch(`/api/conclusions/daily-review?${params.toString()}`, {
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`/api/conclusions/daily-review -> ${res.status}`)
  return res.json()
}

// 已生成复盘的交易日列表 (降序), 供日期下拉。
export async function fetchDailyReviewDates(market: Market): Promise<string[]> {
  const res = await fetch(`/api/conclusions/daily-review/dates?market=${market}`, {
    cache: 'no-store',
  })
  if (!res.ok) throw new Error(`/api/conclusions/daily-review/dates -> ${res.status}`)
  const data = await res.json()
  return (data.dates ?? []) as string[]
}
