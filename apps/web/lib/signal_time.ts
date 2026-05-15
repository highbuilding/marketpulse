import type { AnySignalInterval } from './types'

// 北京时间(UTC+8)的 YYYY-MM-DD,与"自然交易日"对齐 ——
// 5.5 收盘后跨过 BJT 00:00 进入 5.6, 5.5 的 bar 自然进入"历史"。
export function bjtDateKey(iso: string): string {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
}

// 1d bar 的 bar_ts 是 sina 的 "收盘日 16:00 UTC" = BJT 次日 00:00,
// 显示和切分都按"收盘当日"语义处理 —— 把 1d ts 减 8h 再算 BJT 日期。
export function effectiveTsIso(iso: string, interval: AnySignalInterval): string {
  if (interval !== '1d') return iso
  const t = new Date(iso).getTime() - 8 * 3600_000
  return new Date(t).toISOString()
}

export function todayBjtKey(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
}

export function fmtSignalTs(iso: string, interval: AnySignalInterval): string {
  const d = new Date(effectiveTsIso(iso, interval))
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  if (interval === '1d') return `${yyyy}-${mm}-${dd}`
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`
}
