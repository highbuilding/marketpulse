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
