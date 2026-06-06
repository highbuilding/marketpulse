'use client'

import { useRouter } from 'next/navigation'
import { useMemo, useState } from 'react'
import useSWR from 'swr'

import { IntradayLineChart } from '@/components/IntradayLineChart'
import { KLineChart, type SignalMarker } from '@/components/KLineChart'
import { FundFlowPanel } from '@/components/FundFlowPanel'
import { CDSignalPanel } from '@/components/CDSignalPanel'
import { ChipSummaryPanel } from '@/components/ChipSummaryPanel'
import { VolumeIndicatorsPanel } from '@/components/VolumeIndicatorsPanel'
import { fetchChipSummary, fetchSymbolProfile } from '@/lib/symbol_api'
import { listCDSignalsBySymbol } from '@/lib/cd_signals_api'
import { CD_MARKER_INTERVALS, klineTabsForMarket } from '@/lib/intervals'
import { inferMarket, isInTradingSession, isUsRegularSession } from '@/lib/markets'
import { useKlineStream } from '@/lib/use_kline_stream'
import { useBarsHistory, mergeTail } from '@/lib/use_bars_history'
import type { BarDTO, Interval } from '@/lib/types'

const EMPTY_BARS: BarDTO[] = []

export default function SymbolPage({ params }: { params: { code: string } }) {
  const symbol = decodeURIComponent(params.code)
  // crypto default 5m; A 股/美股 default 5m (1m 只有 WS 实时推, 无历史回填)
  const initialInterval: Interval = '5m'
  const [interval, setInterval] = useState<Interval>(initialInterval)
  const router = useRouter()

  // 判断是否指数 (000xxx.SH / 399xxx.SZ),指数不显示资金流面板
  const isIndex = useMemo(() => {
    const [code, mkt] = symbol.split('.')
    if (mkt === 'SH' && code.startsWith('000')) return true
    if (mkt === 'SZ' && code.startsWith('399')) return true
    return false
  }, [symbol])

  const goBack = () => {
    if (typeof window !== 'undefined' && window.history.length > 1) {
      router.back()
    } else {
      router.push('/market')
    }
  }

  const { data: profile } = useSWR(`profile:${symbol}`, () => fetchSymbolProfile(symbol))
  const effectiveMarket = profile?.market ?? inferMarket(symbol)
  const intervalTabs = useMemo(
    () => klineTabsForMarket(effectiveMarket),
    [effectiveMarket],
  )

  // 视图模式: 分时折线(券商口径) vs K 线蜡烛。
  // 美股/A 股默认分时折线; crypto 不做分时, 默认 K 线(且隐藏分时 tab)。
  // 美股非 RTH(盘前/盘后/隔夜/周末): 分时无数据, 默认落 K 线视图。
  const supportsIntraday = effectiveMarket === 'ashare' || effectiveMarket === 'us'
  const usPremarket = effectiveMarket === 'us' && !isUsRegularSession()
  const [viewMode, setViewMode] = useState<'intraday' | 'kline'>(
    usPremarket ? 'kline' : 'intraday',
  )
  const isIntradayView = supportsIntraday && viewMode === 'intraday'
  const isKlineMode = !isIntradayView

  // === 统一数据管道: 历史 REST 分页 + SSE 实时尾部 (三市场通用) ===
  // - useBarsHistory: 游标分页打底 (首屏最新一页 + 滑动翻页), 所有周期通用
  // - useKlineStream: SSE push 实时尾部 (最右一根), 所有市场+周期通用
  // - mergeBarsAsc: 两通道合并, SSE 实时尾部覆盖 REST 历史
  const hist = useBarsHistory(symbol, interval, effectiveMarket, {
    enabled: true,
    poll: false,  // SSE 推送实时尾部, 不需要 REST 轮询
  })

  const streamBars = useKlineStream(symbol, interval, true)  // 所有市场+周期 SSE

  const displayBars: BarDTO[] = useMemo(() => {
    // 首屏历史(REST 500根)未到时不渲染 stream 单根, 消除切周期"先一根后一堆"闪烁
    // mergeTail: hist 稳定前缀 + streamBars 尾部覆盖, 避免每次 SSE tick 全量 sort
    return hist.bars.length === 0 ? [] : mergeTail(hist.bars, streamBars)
  }, [hist.bars, streamBars])

  const { data: chipSummary, error: chipError, isLoading: chipLoading } = useSWR(
    effectiveMarket === 'ashare' ? `chip:${symbol}` : null,
    () => fetchChipSummary(symbol, 90),
  )
  const latestChip = useMemo(
    () => chipSummary && chipSummary.rows.length > 0
      ? chipSummary.rows[chipSummary.rows.length - 1]
      : null,
    [chipSummary],
  )
  const chipLevels = useMemo(() => latestChip ? ({
    avgCost: latestChip.avg_cost,
    cost70Low: latestChip.cost_70_low,
    cost70High: latestChip.cost_70_high,
    cost90Low: latestChip.cost_90_low,
    cost90High: latestChip.cost_90_high,
  }) : null, [latestChip])

  // CD 信号(只对 60m/4h/1d 有意义),用于在 KLineChart 上叠 markers
  const signalInterval = CD_MARKER_INTERVALS.has(interval) ? interval : null
  const { data: signalsResp } = useSWR(
    signalInterval ? `cd:${symbol}:${signalInterval}` : null,
    () => listCDSignalsBySymbol(symbol, signalInterval ? [signalInterval] : undefined),
    { refreshInterval: 60_000 },
  )
  const markers: SignalMarker[] = useMemo(() => {
    if (!signalsResp || displayBars.length === 0) return []
    const minTs = new Date(displayBars[0].ts).getTime()
    const maxTs = new Date(displayBars[displayBars.length - 1].ts).getTime()
    return signalsResp.signals
      .filter((s) => {
        const t = new Date(s.bar_ts).getTime()
        return t >= minTs && t <= maxTs
      })
      .map((s) => ({ ts: s.bar_ts, signal_type: s.signal_type }))
  }, [signalsResp, displayBars])

  // livePrice: 从 SSE 流式 bar 中取末根 close (所有市场通用)
  const livePrice: number | null = useMemo(() => {
    if (displayBars.length === 0) return null
    return displayBars[displayBars.length - 1].close
  }, [displayBars])

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-4">
          <h1 className="text-2xl font-bold">{profile?.name ?? '—'}</h1>
          <span className="text-lg font-mono text-neutral-400">{symbol}</span>
          {profile?.market && (
            <span className="text-xs text-neutral-500 uppercase">{profile.market}</span>
          )}
        </div>
        <button onClick={goBack} className="text-xs text-neutral-400 hover:text-neutral-200">← 返回</button>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <div className="flex gap-1 mb-3">
          {supportsIntraday && (
            <button
              onClick={() => setViewMode('intraday')}
              className={`px-2 py-1 text-xs rounded ${isIntradayView ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'}`}
            >
              分时
            </button>
          )}
          {intervalTabs.map((iv) => (
            <button
              key={iv.key}
              onClick={() => {
                setViewMode('kline')
                setInterval(iv.key)
              }}
              className={`px-2 py-1 text-xs rounded ${!isIntradayView && interval === iv.key ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'}`}
            >
              {iv.label}
            </button>
          ))}
        </div>

        {/* K 线: loading/error 状态 */}
        {isKlineMode && hist.loading && displayBars.length === 0 && (
          <p className="text-sm text-neutral-500">加载中…</p>
        )}
        {isKlineMode && !!hist.error && displayBars.length === 0 && (
          <p className="text-sm text-red-400">加载失败</p>
        )}

        {/* 分时折线模式 (券商口径: 当日逐分钟价格 + 均价线) */}
        {isIntradayView && (
          <IntradayLineChart symbol={symbol} height={420} enabled={isIntradayView} />
        )}

        {/* K 线模式 */}
        {isKlineMode && displayBars.length > 0 && (
          <KLineChart
            bars={displayBars}
            interval={interval}
            market={effectiveMarket}
            height={420}
            signals={signalInterval ? markers : undefined}
            livePrice={isKlineMode ? livePrice : null}
            chipLevels={chipLevels}
            onLoadMore={hist.loadMore}
            hasMore={hist.hasMore}
            loadingMore={hist.loadingMore}
          />
        )}
        {isKlineMode && !hist.loading && displayBars.length === 0 && (
          <p className="text-sm text-yellow-400">无数据。请先 <code>make warmup</code> 或本周期还未抓取。</p>
        )}
      </section>

      {effectiveMarket === 'ashare' && (
        <ChipSummaryPanel
          data={chipSummary}
          error={chipError}
          isLoading={chipLoading}
          currentPrice={livePrice}
        />
      )}
      <VolumeIndicatorsPanel symbol={symbol} interval={interval} />
      {!isIndex && <FundFlowPanel symbol={symbol} />}
      <CDSignalPanel symbol={symbol} market={effectiveMarket} />
    </main>
  )
}
