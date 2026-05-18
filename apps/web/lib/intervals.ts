// 与后端 core/domain/intervals.py 保持一致 — 单一事实源镜像。
// 改这里时同步改后端。

import type { AnySignalInterval, DetailSignalInterval, Interval } from './types'

export interface IntervalSpec {
  key: Interval
  labelCn: string
  isKline: boolean       // 暴露给 K 线 chart / bars 接口
  isSignal: boolean      // 会被 CD 扫描
  cryptoOnly: boolean    // 历史字段, 已废弃: 4h tab 可见性由前端按 market 控制
}

export const INTERVAL_SPECS: IntervalSpec[] = [
  { key: '1m',  labelCn: '分时',   isKline: true,  isSignal: false, cryptoOnly: false },
  { key: '5m',  labelCn: '5分',    isKline: true,  isSignal: false, cryptoOnly: false },
  { key: '15m', labelCn: '15分',   isKline: true,  isSignal: true,  cryptoOnly: false },
  { key: '30m', labelCn: '30分',   isKline: true,  isSignal: true,  cryptoOnly: false },
  { key: '60m', labelCn: '1小时',  isKline: true,  isSignal: true,  cryptoOnly: false },
  { key: '4h',  labelCn: '4小时',  isKline: true,  isSignal: true,  cryptoOnly: false },
  { key: '1d',  labelCn: '日线',   isKline: true,  isSignal: true,  cryptoOnly: false },
  { key: '1wk', labelCn: '周线',   isKline: true,  isSignal: false, cryptoOnly: false },
  { key: '1mo', labelCn: '月线',   isKline: true,  isSignal: false, cryptoOnly: false },
]

const BY_KEY: Record<string, IntervalSpec> = Object.fromEntries(
  INTERVAL_SPECS.map((s) => [s.key, s]),
)

export function intervalLabel(key: Interval): string {
  return BY_KEY[key]?.labelCn ?? key
}

// K 线 tab(详情页用): 4h 仅 us+crypto 显示
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  const allowFourH = market === 'us' || market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isKline && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key, label: s.labelCn }))
}

// 详情页 CDSignalPanel tab: 信号周期, 4h 仅 us+crypto 显示
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  const allowFourH = market === 'us' || market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isSignal && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
}

// 关注页 WatchlistSignalsPanel tab: 全部信号周期, 4h 由调用方按 market 过滤
export function allSignalTabs(): { key: AnySignalInterval; label: string }[] {
  return INTERVAL_SPECS
    .filter((s) => s.isSignal)
    .map((s) => ({ key: s.key as AnySignalInterval, label: s.labelCn }))
}

// 哪些 interval 在 K 线 chart 上有 CD markers
export const CD_MARKER_INTERVALS: Set<Interval> = new Set(
  INTERVAL_SPECS.filter((s) => s.isSignal).map((s) => s.key),
)
