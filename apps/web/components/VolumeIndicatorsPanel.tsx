'use client'

import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'

import { fetchVolumeIndicators } from '@/lib/symbol_api'
import type { Interval, VolumeIndicatorRow } from '@/lib/types'

const VOLUME_INTERVALS: { key: Interval; label: string; hint: string }[] = [
  { key: '5m', label: '5分', hint: '盘中异动' },
  { key: '15m', label: '15分', hint: '短线节奏' },
  { key: '30m', label: '30分', hint: '半小时结构' },
  { key: '60m', label: '1小时', hint: '日内趋势' },
  { key: '1d', label: '日线', hint: '趋势确认' },
]
const SUPPORTED: ReadonlySet<Interval> = new Set(VOLUME_INTERVALS.map((x) => x.key))

function defaultVolumeInterval(chartInterval: Interval): Interval {
  if (SUPPORTED.has(chartInterval)) return chartInterval
  if (chartInterval === '1m') return '5m'
  return '1d'
}

function daysForInterval(interval: Interval): number {
  if (interval === '1d') return 120
  return 10
}

function fmtVolume(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(0)
}

function fmtAmount(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '待补齐'
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(0)
}

function fmtRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${v.toFixed(2)}x`
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '待补齐'
  const pct = Math.abs(v) <= 1 ? v * 100 : v
  return `${pct.toFixed(2)}%`
}

function latest(rows: VolumeIndicatorRow[]): VolumeIndicatorRow | null {
  return rows.length > 0 ? rows[rows.length - 1] : null
}

export function VolumeIndicatorsPanel({
  symbol,
  interval,
}: {
  symbol: string
  interval: Interval
}) {
  const initialInterval = useMemo(() => defaultVolumeInterval(interval), [interval])
  const [indicatorInterval, setIndicatorInterval] = useState<Interval>(initialInterval)
  useEffect(() => {
    setIndicatorInterval(initialInterval)
  }, [initialInterval, symbol])
  const intervalMeta = VOLUME_INTERVALS.find((x) => x.key === indicatorInterval)
  const days = daysForInterval(indicatorInterval)
  const { data, error, isLoading } = useSWR(
    `volume-indicators:${symbol}:${indicatorInterval}`,
    () => fetchVolumeIndicators(symbol, indicatorInterval, days),
    { refreshInterval: indicatorInterval === '1d' ? 0 : 60_000 },
  )
  const row = data ? latest(data.rows) : null
  const volumeState = classifyVolume(row)
  const obvState = classifyObv(data?.rows ?? [])
  const stateTone = volumeState.tone
  const unitLabel = indicatorInterval === '1d' ? '日' : '根'
  const summary = row
    ? `${volumeState.label}：量比 ${fmtRatio(row.volume_ratio)}，单根放量 ${fmtRatio(row.single_bar_volume_ratio)}，当前成交速度${volumeRatioText(row.volume_ratio)}。OBV 近 5 根${obvState}。`
    : null

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
      <div className="flex flex-col gap-3 mb-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-200">量能指标</h2>
          <p className="mt-1 text-xs text-neutral-500">
            {intervalMeta?.hint ?? '量价确认'} · 最近 {indicatorInterval === '1d' ? '120 个交易日' : `${days} 个交易日`}，按当前选择周期计算
          </p>
        </div>
        <div className="flex flex-wrap gap-1 rounded-md bg-neutral-900 p-1">
          {VOLUME_INTERVALS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setIndicatorInterval(item.key)}
              className={`h-7 px-2 text-xs rounded transition-colors ${
                indicatorInterval === item.key
                  ? 'bg-neutral-700 text-white'
                  : 'text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200'
              }`}
              title={item.hint}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {Boolean(error) && <p className="text-sm text-red-400">量能指标加载失败。</p>}
      {!isLoading && !Boolean(error) && !row && (
        <p className="text-sm text-neutral-500">暂无量能指标数据。</p>
      )}
      {row && (
        <div className="space-y-4">
          <div className="rounded-md border border-neutral-800 bg-neutral-900/40 p-3">
            <div className="text-xs text-neutral-500 mb-1">解读</div>
            <p className="text-sm text-neutral-200 leading-6">{summary}</p>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <Metric label="量能状态" value={volumeState.label} tone={stateTone} />
            <Metric label="量比" value={fmtRatio(row.volume_ratio)} />
            <Metric label="单根放量" value={fmtRatio(row.single_bar_volume_ratio)} />
            <Metric label="成交量" value={fmtVolume(row.volume)} />
            <Metric label={`5${unitLabel}均量`} value={fmtVolume(row.vol_ma5)} />
            <Metric label={`20${unitLabel}均量`} value={fmtVolume(row.vol_ma20)} />
            <Metric label="成交额" value={fmtAmount(row.amount)} />
            <Metric label={`20${unitLabel}均额`} value={fmtAmount(row.amount_ma20)} />
            <Metric label="换手率" value={fmtPct(row.turnover)} />
            <Metric label="OBV 趋势" value={obvState} />
            <Metric label="OBV 当前值" value={fmtVolume(row.obv)} />
          </div>
          <p className="text-xs text-neutral-500 leading-5">
            量比对齐券商口径：分钟周期按当日截至当前累计成交速度 / 过去 5 个交易日同进度平均成交速度；日线按当前成交量 / 前 5 日均量。单根放量按当前这根 K 线成交量 / 前 20 根均量计算，放量上涨和缩量回落用单根放量判断。成交额和换手率依赖数据源，当前 A 股日线最完整。
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

function classifyVolume(row: VolumeIndicatorRow | null): {
  label: string
  description: string
  tone: string
} {
  if (
    !row
    || (
      (row.volume_ratio == null || !Number.isFinite(row.volume_ratio))
      && (
        row.single_bar_volume_ratio == null
        || !Number.isFinite(row.single_bar_volume_ratio)
      )
    )
  ) {
    return { label: '待判断', description: '待补齐', tone: 'text-neutral-300' }
  }
  if (row.is_volume_breakout) {
    return { label: '放量上涨', description: '明显放大', tone: 'text-red-300' }
  }
  if (row.is_shrink_pullback) {
    return { label: '缩量回落', description: '明显收缩', tone: 'text-green-300' }
  }
  if (row.single_bar_volume_ratio != null && row.single_bar_volume_ratio >= 1.2) {
    return { label: '温和放量', description: '温和放大', tone: 'text-red-200' }
  }
  if (row.single_bar_volume_ratio != null && row.single_bar_volume_ratio <= 0.8) {
    return { label: '缩量', description: '低于均量', tone: 'text-green-200' }
  }
  return { label: '常态', description: '接近均量', tone: 'text-neutral-300' }
}

function volumeRatioText(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '待判断'
  if (v >= 1.2) return '高于过去 5 日均速'
  if (v <= 0.8) return '低于过去 5 日均速'
  return '接近过去 5 日均速'
}

function classifyObv(rows: VolumeIndicatorRow[]): string {
  if (rows.length < 6) return '待判断'
  const latestRow = rows[rows.length - 1]
  const prev = rows[rows.length - 6]
  if (!Number.isFinite(latestRow.obv) || !Number.isFinite(prev.obv)) return '待判断'
  if (latestRow.obv > prev.obv) return '走强'
  if (latestRow.obv < prev.obv) return '走弱'
  return '持平'
}
