// SSoT 镜像: 改这里时同步改后端 core/domain/markets.py。

export type Market = 'ashare' | 'hk' | 'us' | 'crypto'

export function inferMarket(symbol: string): Market {
  if (/\.(SH|SZ|BJ)$/.test(symbol)) return 'ashare'
  if (symbol.endsWith('.HK')) return 'hk'
  if (symbol.includes('/')) return 'crypto'
  return 'us'
}

const TZ: Record<Market, string> = {
  ashare: 'Asia/Shanghai',
  hk:     'Asia/Hong_Kong',
  us:     'America/New_York',
  crypto: 'Asia/Shanghai',  // crypto 沿用 BJT 惯例
}

export function marketTz(market: Market): string {
  return TZ[market]
}

export function tradingDateKey(iso: string, market: Market): string {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: TZ[market] })
}

export function todayKey(market: Market): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: TZ[market] })
}

// 给定 ISO 时刻, 返回该市场时区相对 UTC 的 offset(秒)。
// 用 Intl.DateTimeFormat 提取 offset, 而非常量, 以处理夏/冬令时。
export function tzOffsetSeconds(market: Market, iso: string): number {
  const date = new Date(iso)
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ[market],
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  })
  const parts = fmt.formatToParts(date)
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value)
  // 部分 locale 用 24:00 表示次日 00:00 — 直接交给 Date.UTC 处理参数溢出(hour=24 → day+1, hour=0)
  const localAsUtc = Date.UTC(
    get('year'), get('month') - 1, get('day'),
    get('hour'),
    get('minute'), get('second'),
  )
  return (localAsUtc - date.getTime()) / 1000
}

// 市场 session 表(SSoT 镜像: core/domain/market_sessions.py::SESSIONS)。
// 用于前端判断当前是否在交易时段(决定是否显示 placeholder K 线)。
const SESSIONS: Record<Market, [string, string][]> = {
  ashare: [['09:30', '11:30'], ['13:00', '15:00']],
  hk:     [['09:30', '12:00'], ['13:00', '16:00']],
  us:     [['04:00', '09:30'], ['09:30', '16:00'], ['16:00', '20:00']],
  crypto: [['00:00', '24:00']],
}

function _hhmmToMinutes(s: string): number {
  const [h, m] = s.split(':').map(Number)
  return h * 60 + m
}

// 当前是否在本市场交易时段(本市场时区, 周一-五;crypto 24/7)
export function isMarketOpenNow(market: Market): boolean {
  const now = new Date()
  // 本市场时区当前 hh:mm + day-of-week
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ[market],
    weekday: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
  const parts = fmt.formatToParts(now)
  const weekday = parts.find((p) => p.type === 'weekday')?.value ?? ''
  const hour = Number(parts.find((p) => p.type === 'hour')?.value ?? 0)
  const minute = Number(parts.find((p) => p.type === 'minute')?.value ?? 0)
  const minutesNow = hour * 60 + minute

  if (market === 'crypto') return true
  if (weekday === 'Sat' || weekday === 'Sun') return false

  for (const [start, end] of SESSIONS[market]) {
    const sMin = _hhmmToMinutes(start)
    const eMin = end === '24:00' ? 24 * 60 : _hhmmToMinutes(end)
    if (minutesNow >= sMin && minutesNow <= eMin) return true
  }
  return false
}

// 给定 ISO 时刻 (UTC) 是否落在本市场交易时段。用于过滤 1m bars 的脏数据,
// 防止凌晨/午休的点出现在分时图。crypto 永远 true。不判节假日。
export function isInTradingSession(iso: string, market: Market): boolean {
  if (market === 'crypto') return true
  const d = new Date(iso)
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ[market],
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
  const parts = fmt.formatToParts(d)
  const hour = Number(parts.find((p) => p.type === 'hour')?.value ?? 0)
  const minute = Number(parts.find((p) => p.type === 'minute')?.value ?? 0)
  const minutesAt = hour * 60 + minute
  for (const [start, end] of SESSIONS[market]) {
    const sMin = _hhmmToMinutes(start)
    const eMin = end === '24:00' ? 24 * 60 : _hhmmToMinutes(end)
    if (minutesAt >= sMin && minutesAt <= eMin) return true
  }
  return false
}
