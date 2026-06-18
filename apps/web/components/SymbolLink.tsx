'use client'

import type { CSSProperties } from 'react'
import Link from 'next/link'

// 盘面榜单/异动里的个股下钻。collected=true 可点进 /symbol/{code} 详情;
// collected=false 灰显 + 提示"不在采集范围"(不在采集池的标的不落 K线/分时, 见后端口径)。
export function SymbolLink({
  symbol, name, collected, style,
}: {
  symbol: string
  name?: string | null
  collected: boolean
  style?: CSSProperties
}) {
  const label = name || symbol
  if (collected) {
    return (
      <Link
        href={`/symbol/${encodeURIComponent(symbol)}`}
        style={{ color: 'var(--accent)', textDecoration: 'none', ...style }}
        title={symbol}
      >
        {label}
      </Link>
    )
  }
  return (
    <span
      style={{ color: 'var(--text2)', cursor: 'not-allowed', ...style }}
      title={`${symbol} · 不在采集范围,无法查看详情`}
    >
      {label}
    </span>
  )
}
