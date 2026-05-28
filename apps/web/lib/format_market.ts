/**
 * 大盘指数 IndexCard 数字格式化工具。
 * SSoT: 给 4 个市场 IndexCard 用同一份格式化逻辑。
 */

/** 资金净流入: 单位"亿", 正负号 + 1 位小数。例: 12.3 → "+12.3亿", -3.567 → "-3.6亿" */
export function formatFundInflow(yiyuan: number): string {
  const sign = yiyuan >= 0 ? '+' : ''
  return `${sign}${yiyuan.toFixed(1)}亿`
}

/** 成交额: 整数 + 单位标签。例: 8421.5 + "亿元" → "8421亿元" */
export function formatAmount(value: number, unit: string): string {
  // 大于 1 万亿时显示"X.XX 万亿"更友好
  if (value >= 10000) {
    return `${(value / 10000).toFixed(2)}万${unit.replace(/^亿/, '亿')}`
  }
  return `${Math.round(value)}${unit}`
}

/** 同比百分比: 1 位小数 + 正负号。例: 0.0521 → "+5.2%", -0.123 → "-12.3%" */
export function formatRatioPct(ratio: number): string {
  const pct = ratio * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(1)}%`
}
