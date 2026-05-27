'use client'

import type { ChipSummaryResponse, ChipSummaryRow } from '@/lib/types'

function fmtPrice(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return v >= 100 ? v.toFixed(2) : v.toFixed(3)
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const pct = Math.abs(v) <= 1 ? v * 100 : v
  return `${pct.toFixed(1)}%`
}

function latest(rows: ChipSummaryRow[]): ChipSummaryRow | null {
  return rows.length > 0 ? rows[rows.length - 1] : null
}

export function ChipSummaryPanel({
  data,
  isLoading,
  error,
  currentPrice,
}: {
  data?: ChipSummaryResponse
  isLoading?: boolean
  error?: unknown
  currentPrice?: number | null
}) {
  const row = data ? latest(data.rows) : null
  const profitPct = normalizePct(row?.profit_ratio)
  const concentration90Pct = normalizePct(row?.concentration_90)
  const concentrationLabel = concentrationText(concentration90Pct)
  const pricePosition = pricePositionText(currentPrice, row?.avg_cost)
  const summary = row
    ? `当前约 ${fmtPct(row.profit_ratio)} 筹码处于获利状态，90% 筹码分布${concentrationLabel}。${pricePosition}`
    : null
  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-semibold text-neutral-200">筹码分布</h2>
        <span className="text-xs text-neutral-500">
          {row ? new Date(row.trade_date).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' }) : '日线'}
        </span>
      </div>
      {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {Boolean(error) && <p className="text-sm text-red-400">筹码数据加载失败。</p>}
      {!isLoading && !Boolean(error) && !row && (
        <p className="text-sm text-neutral-500">暂无筹码分布数据。仅 A 股支持，首次访问会按需拉取。</p>
      )}
      {row && (
        <div className="space-y-4">
          <div className="rounded-md border border-neutral-800 bg-neutral-900/40 p-3">
            <div className="text-xs text-neutral-500 mb-1">解读</div>
            <p className="text-sm text-neutral-200 leading-6">{summary}</p>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <Metric label="获利比例" value={fmtPct(row.profit_ratio)} tone={profitPct != null && profitPct >= 50 ? 'text-red-300' : 'text-green-300'} />
            <Metric label="平均成本" value={fmtPrice(row.avg_cost)} />
            <Metric label="90% 集中度" value={`${fmtPct(row.concentration_90)} · ${concentrationLabel}`} />
            <Metric label="当前价位置" value={pricePositionShort(currentPrice, row.avg_cost)} />
            <Metric label="70% 成本区间" value={`${fmtPrice(row.cost_70_low)} - ${fmtPrice(row.cost_70_high)}`} />
            <Metric label="90% 成本区间" value={`${fmtPrice(row.cost_90_low)} - ${fmtPrice(row.cost_90_high)}`} />
            <Metric label="70% 集中度" value={fmtPct(row.concentration_70)} />
            <Metric label="数据周期" value="日线" />
          </div>
          <p className="text-xs text-neutral-500 leading-5">
            集中度表示筹码成本区间的宽窄，数值越小，说明持仓成本越集中；这里只展示东方财富日线摘要，不做分时筹码变化。
          </p>
        </div>
      )}
    </section>
  )
}

function Metric({ label, value, tone = 'text-neutral-100' }: {
  label: string
  value: string
  tone?: string
}) {
  return (
    <div>
      <div className="text-xs text-neutral-500 mb-1">{label}</div>
      <div className={`font-mono tabular-nums ${tone}`}>{value}</div>
    </div>
  )
}

function normalizePct(v: number | null | undefined): number | null {
  if (v == null || !Number.isFinite(v)) return null
  return Math.abs(v) <= 1 ? v * 100 : v
}

function concentrationText(v: number | null): string {
  if (v == null) return '待判断'
  if (v <= 12) return '较集中'
  if (v <= 20) return '一般'
  return '较分散'
}

function pricePositionText(price: number | null | undefined, avgCost: number | null | undefined): string {
  if (price == null || avgCost == null || !Number.isFinite(price) || !Number.isFinite(avgCost)) {
    return '当前价位置待补充。'
  }
  if (price >= avgCost) return `当前价高于平均成本 ${fmtPrice(avgCost)}。`
  return `当前价低于平均成本 ${fmtPrice(avgCost)}。`
}

function pricePositionShort(price: number | null | undefined, avgCost: number | null | undefined): string {
  if (price == null || avgCost == null || !Number.isFinite(price) || !Number.isFinite(avgCost)) return '待补充'
  return price >= avgCost ? '高于平均成本' : '低于平均成本'
}
