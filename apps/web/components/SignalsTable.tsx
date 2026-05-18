'use client'

import useSWR from 'swr'

import { fetchSymbolProfile } from '@/lib/symbol_api'
import { fmtSignalTs } from '@/lib/signal_time'
import type { Market } from '@/lib/markets'
import type { AnySignalInterval, CDSignalDTO } from '@/lib/types'

function SymbolNameCell({ symbol }: { symbol: string }) {
  // 信号流场景下 SWR cache 已被 fetchSymbolProfiles 预填充,这里直接命中。
  // 详情页 CDSignalPanel 不会渲染 symbol 列, 不进这里。
  const { data } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))
  return <span className="text-sm text-neutral-300">{data?.name ?? '—'}</span>
}

export function SignalsTable({
  signals,
  interval,
  market,
  showSymbol = false,
}: {
  signals: CDSignalDTO[]
  interval: AnySignalInterval
  market: Market
  showSymbol?: boolean
}) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-neutral-400 text-xs">
          <th className="text-left py-1">时间</th>
          {showSymbol && <th className="text-left">标的</th>}
          <th className="text-left">类型</th>
          <th className="text-right">价格</th>
          <th className="text-right">DIF</th>
        </tr>
      </thead>
      <tbody>
        {signals.map((s) => {
          const isBuy = s.signal_type === 'buy'
          return (
            <tr key={s.id} className="border-t border-neutral-800">
              <td className="py-1 font-mono text-neutral-300 whitespace-nowrap">
                {fmtSignalTs(s.bar_ts, interval, market)}
              </td>
              {showSymbol && (
                <td>
                  <a
                    href={`/symbol/${encodeURIComponent(s.symbol)}`}
                    className="flex items-baseline gap-2 hover:text-blue-400"
                  >
                    <span className="font-mono text-xs">{s.symbol}</span>
                    <SymbolNameCell symbol={s.symbol} />
                  </a>
                </td>
              )}
              <td>
                <span
                  className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${
                    isBuy
                      ? 'bg-red-900/40 text-red-400'
                      : 'bg-green-900/40 text-green-400'
                  }`}
                >
                  {isBuy ? '抄底' : '卖出'}
                </span>
              </td>
              <td className="text-right tabular-nums">{s.price.toFixed(2)}</td>
              <td className="text-right tabular-nums text-neutral-500">
                {s.d_value != null ? s.d_value.toFixed(3) : '—'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
