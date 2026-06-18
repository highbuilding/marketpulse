// SSoT 镜像: 改这里时同步改后端 core/domain/markets.py。

export type Market = 'ashare' | 'hk' | 'us' | 'crypto'

// 裸 A 股 6 位数字码补后缀 (SSoT 镜像: core/domain/markets.py::normalize_symbol)。
// 600519 → 600519.SH, 000858 → 000858.SZ, 920001 → 920001.BJ。其余原样。
// 用户搜索常输裸码, 不补后缀会导致 profile 查不到名称 + 市场误判成 us。
export function normalizeSymbol(symbol: string): string {
  if (/^\d{6}$/.test(symbol)) {
    if (/^(60|68|51|50|11|13)/.test(symbol)) return `${symbol}.SH`
    if (/^([48]|920)/.test(symbol)) return `${symbol}.BJ`
    return `${symbol}.SZ`
  }
  return symbol
}

export function inferMarket(symbol: string): Market {
  if (/\.(SH|SZ|BJ)$/.test(symbol)) return 'ashare'
  if (symbol.endsWith('.HK')) return 'hk'
  if (symbol.includes('/')) return 'crypto'
  if (/-(USDT|USDC|BUSD|FDUSD)$/.test(symbol)) return 'crypto'
  if (/^\d{6}$/.test(symbol)) return 'ashare'
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

// 市场当前时段细分状态(本市场时区)。用于 TopBar 展示开盘/盘前/午休/盘后/休市。
export type MarketPhase = 'pre' | 'open' | 'lunch' | 'after' | 'closed'

const PHASE_LABEL: Record<MarketPhase, string> = {
  pre: '盘前', open: '交易中', lunch: '午间休市', after: '盘后', closed: '已休市',
}
export function marketPhaseLabel(p: MarketPhase): string {
  return PHASE_LABEL[p]
}

export function marketPhase(market: Market, now: Date = new Date()): MarketPhase {
  if (market === 'crypto') return 'open' // 7x24
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ[market], weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(now)
  const wd = parts.find((p) => p.type === 'weekday')?.value ?? ''
  if (wd === 'Sat' || wd === 'Sun') return 'closed'
  const hh = Number(parts.find((p) => p.type === 'hour')?.value ?? '0') % 24
  const mm = Number(parts.find((p) => p.type === 'minute')?.value ?? '0')
  const t = hh * 60 + mm

  // 美股:盘前 04:00-09:30 / RTH 09:30-16:00 / 盘后 16:00-20:00
  if (market === 'us') {
    if (t < 4 * 60) return 'closed'
    if (t < 9 * 60 + 30) return 'pre'
    if (t < 16 * 60) return 'open'
    if (t < 20 * 60) return 'after'
    return 'closed'
  }

  // A股/港股:盘前 + 交易中 + 午间休市 + 盘后
  const sess = SESSIONS[market]
  const first = _hhmmToMinutes(sess[0][0])
  const lastMin = _hhmmToMinutes(sess[sess.length - 1][1])
  if (t < first) return 'pre'
  if (t >= lastMin) return 'after'
  for (const [s, e] of sess) {
    if (t >= _hhmmToMinutes(s) && t < _hhmmToMinutes(e)) return 'open'
  }
  return 'lunch' // 在首末之间但不在任何 session 内 = 午休
}

// A 股盘面细分时段 (镜像后端 core/domain/market_sessions.py::ashare_phase)。
// 驱动盘面面板模块显隐/高亮。比 marketPhase 更细 (拆出竞价/开盘/尾盘)。
export type AshareBoardPhase =
  | 'pre' | 'auction' | 'open' | 'intraday' | 'closing' | 'post' | 'closed'

const ASHARE_PHASE_LABEL: Record<AshareBoardPhase, string> = {
  pre: '盘前', auction: '集合竞价', open: '开盘', intraday: '盘中',
  closing: '尾盘竞价', post: '盘后', closed: '休市',
}
export function ashareBoardPhaseLabel(p: AshareBoardPhase): string {
  return ASHARE_PHASE_LABEL[p]
}

export function ashareBoardPhase(now: Date = new Date()): AshareBoardPhase {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(now)
  const wd = parts.find((p) => p.type === 'weekday')?.value ?? ''
  if (wd === 'Sat' || wd === 'Sun') return 'closed'
  const hh = Number(parts.find((p) => p.type === 'hour')?.value ?? '0') % 24
  const mm = Number(parts.find((p) => p.type === 'minute')?.value ?? '0')
  const t = hh * 60 + mm
  if (t < 9 * 60 + 15) return 'pre'
  if (t < 9 * 60 + 30) return 'auction'
  if (t < 9 * 60 + 40) return 'open'
  if (t < 14 * 60 + 55) return 'intraday'
  if (t <= 15 * 60) return 'closing'
  return 'post'
}

// 美股 RTH 判定 (09:30-16:00 ET)。分时图仅 RTH; 非 RTH 详情页默认 K 线。
export function isUsRegularSession(now: Date = new Date()): boolean {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour12: false,
    hour: '2-digit', minute: '2-digit', weekday: 'short',
  }).formatToParts(now)
  const wd = parts.find((p) => p.type === 'weekday')?.value
  if (wd === 'Sat' || wd === 'Sun') return false
  const hh = Number(parts.find((p) => p.type === 'hour')?.value ?? '0') % 24
  const mm = Number(parts.find((p) => p.type === 'minute')?.value ?? '0')
  const mins = hh * 60 + mm
  return mins >= 9 * 60 + 30 && mins < 16 * 60
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
