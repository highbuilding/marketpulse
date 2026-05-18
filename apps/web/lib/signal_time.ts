import type { AnySignalInterval } from './types'
import type { Market } from './markets'
import { marketTz, todayKey, tradingDateKey } from './markets'

// adapter 已把 1d ts normalize 为本市场自然交易日 00:00, 这里直通。
export function effectiveTsIso(iso: string, _interval: AnySignalInterval): string {
  return iso
}

// BJT 自然日 key, 保留作 A 股专用别名(老代码兼容)
export function bjtDateKey(iso: string): string {
  return tradingDateKey(iso, 'ashare')
}

export function todayBjtKey(): string {
  return todayKey('ashare')
}

// Market-aware: 按市场时区切分交易日 key
export function marketDateKey(iso: string, market: Market): string {
  return tradingDateKey(iso, market)
}

export function fmtSignalTs(
  iso: string,
  interval: AnySignalInterval,
  market: Market = 'ashare',
): string {
  const tz = marketTz(market)
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
  const parts = fmt.formatToParts(new Date(iso))
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  if (interval === '1d') {
    return `${get('year')}-${get('month')}-${get('day')}`
  }
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`
}
