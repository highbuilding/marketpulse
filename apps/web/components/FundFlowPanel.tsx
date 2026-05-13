'use client'

import useSWR from 'swr'

import { fetchSymbolFundFlow } from '@/lib/symbol_api'

function fmt(v: number | null): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(0)
}

export function FundFlowPanel({ symbol }: { symbol: string }) {
  const { data, isLoading } = useSWR(
    `fund:${symbol}`, () => fetchSymbolFundFlow(symbol, 30),
    { refreshInterval: 60_000 },
  )

  return (
    <section className="rounded-lg border border-neutral-800 p-4 bg-neutral-950">
      <h2 className="text-lg font-semibold mb-3">资金流(近 30 日)</h2>
      {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {data && data.rows.length === 0 && (
        <p className="text-sm text-neutral-500">暂无数据。需先 <code>make warmup</code> 或等待 scheduler 拉取。</p>
      )}
      {data && data.rows.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-neutral-400 text-xs">
              <th className="text-left py-1">日期</th>
              <th className="text-right">主力净流入</th>
              <th className="text-right">超大单</th>
              <th className="text-right">大单</th>
              <th className="text-right">中单</th>
              <th className="text-right">小单</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.slice(-15).reverse().map((r) => (
              <tr key={r.ts} className="border-t border-neutral-800">
                <td className="py-1 font-mono">{r.ts.slice(0, 10)}</td>
                <td className={`text-right tabular-nums ${(r.main_net ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'}`}>{fmt(r.main_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.super_large_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.large_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.medium_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.small_net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
