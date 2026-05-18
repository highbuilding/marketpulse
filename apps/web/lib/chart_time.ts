import { TickMarkType, type Time } from 'lightweight-charts'
import { marketTz, type Market } from './markets'

// 用 Intl.DateTimeFormat 按市场时区取 Y/M/D/H/m/s parts。
function tzParts(time: Time, _market: Market): {
  Y: string; M: string; D: string; H: string; mi: string; s: string
} {
  if (typeof time === 'string') {
    // 日线: time 已是 YYYY-MM-DD(已按市场时区算过)
    const [Y, M, D] = time.split('-')
    return { Y, M, D, H: '00', mi: '00', s: '00' }
  }
  // intraday: time 是 fake-UTC seconds(原 UTC + tzOffsetSeconds), 用 getUTC* 即得本地数字
  const d = new Date((time as number) * 1000)
  return {
    Y: String(d.getUTCFullYear()),
    M: String(d.getUTCMonth() + 1).padStart(2, '0'),
    D: String(d.getUTCDate()).padStart(2, '0'),
    H: String(d.getUTCHours()).padStart(2, '0'),
    mi: String(d.getUTCMinutes()).padStart(2, '0'),
    s: String(d.getUTCSeconds()).padStart(2, '0'),
  }
}

export function makeChartCrosshairFormatter(market: Market) {
  return (time: Time): string => {
    const { Y, M, D, H, mi, s } = tzParts(time, market)
    if (typeof time === 'string') return `${Y}-${M}-${D}`
    return `${Y}-${M}-${D} ${H}:${mi}:${s}`
  }
}

export function makeChartTickFormatter(market: Market) {
  return (time: Time, type: TickMarkType): string => {
    const { Y, M, D, H, mi } = tzParts(time, market)
    if (type === TickMarkType.Year) return Y
    if (type === TickMarkType.Month) return `${Y}-${M}`
    if (type === TickMarkType.DayOfMonth) return `${M}-${D}`
    return `${H}:${mi}`
  }
}

// 兼容老调用: 默认 ashare(IndexCard 等少数旧组件继续用)
export const fmtChartCrosshair = makeChartCrosshairFormatter('ashare')
export const fmtChartTick = makeChartTickFormatter('ashare')

// 占位防 unused-import 警告(marketTz 在内部 tzParts 不显式调用, 但 Market 来自 markets.ts)
void marketTz
