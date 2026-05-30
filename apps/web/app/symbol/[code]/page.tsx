'use client'

import { useRouter } from 'next/navigation'
import { useMemo, useState } from 'react'
import useSWR from 'swr'

import { IntradayChart } from '@/components/IntradayChart'
import { KLineChart, type SignalMarker } from '@/components/KLineChart'
import { FundFlowPanel } from '@/components/FundFlowPanel'
import { CDSignalPanel } from '@/components/CDSignalPanel'
import { ChipSummaryPanel } from '@/components/ChipSummaryPanel'
import { VolumeIndicatorsPanel } from '@/components/VolumeIndicatorsPanel'
import { fetchBars, fetchChipSummary, fetchSymbolProfile } from '@/lib/symbol_api'
import { listCDSignalsBySymbol } from '@/lib/cd_signals_api'
import { CD_MARKER_INTERVALS, klineTabsForMarket } from '@/lib/intervals'
import { inferMarket, isInTradingSession } from '@/lib/markets'
import { useKlineStream } from '@/lib/use_kline_stream'
import { useBarsHistory, mergeBarsAsc } from '@/lib/use_bars_history'
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

  const isIntraday = ['1m', '5m', '15m', '30m', '60m'].includes(interval)
  const isKlineMode = interval !== '1m'  // 1m 用 IntradayChart, 其他用 KLineChart

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
    return mergeBarsAsc(hist.bars, streamBars)
  }, [hist.bars, streamBars])

  // 1m 分时: 从 displayBars 过滤当日 bars
  const todayBars = useMemo(() => {
    if (interval !== '1m' || displayBars.length === 0) return []
    const lastBar = displayBars[displayBars.length - 1]
    const lastDate = new Date(lastBar.ts).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
    return displayBars.filter((b) => {
      const bDate = new Date(b.ts).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
      return bDate === lastDate
    })
  }, [displayBars, interval])

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

  // 分时模式:拉日线最后一根作 prevClose,只展示当日
  const { data: daily } = useSWR(
    interval === '1m' ? `bars:${symbol}:1d:5` : null,
    () => fetchBars(symbol, '1d', 5),
  )
  const prevClose = useMemo(() => {
    if (!daily || daily.bars.length < 2) return null
    return daily.bars[daily.bars.length - 2]?.close ?? daily.bars[daily.bars.length - 1]?.close
  }, [daily])

  // livePrice: 从 SSE 流式 bar 中取末根 close (所有市场通用)
  const livePrice: number | null = useMemo(() => {
    const bars = displayBars.length > 0 ? displayBars
      : (interval === '1m' ? todayBars : null)
    if (!bars || bars.length === 0) return null
    return bars[bars.length - 1].close
  }, [displayBars, interval, todayBars])

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
          {intervalTabs.map((iv) => (
            <button
              key={iv.key}
              onClick={() => setInterval(iv.key)}
              className={`px-2 py-1 text-xs rounded ${interval === iv.key ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'}`}
            >
              {iv.label}
            </button>
          ))}
        </div>

        {/* 1m 分时: loading/error 状态 */}
        {interval === '1m' && hist.loading && todayBars.length === 0 && (
          <p className="text-sm text-neutral-500">加载中…</p>
        )}
        {interval === '1m' && !!hist.error && todayBars.length === 0 && (
          <p className="text-sm text-red-400">加载失败</p>
        )}
        {/* K 线: loading/error 状态 */}
        {isKlineMode && hist.loading && displayBars.length === 0 && (
          <p className="text-sm text-neutral-500">加载中…</p>
        )}
        {isKlineMode && !!hist.error && displayBars.length === 0 && (
          <p className="text-sm text-red-400">加载失败</p>
        )}

        {/* 分时模式 */}
        {interval === '1m' && todayBars.length > 0 && (
          <IntradayChart
            bars={todayBars}
            prevClose={prevClose}
            height={420}
            market={effectiveMarket}
          />
        )}
        {interval === '1m' && !hist.loading && todayBars.length === 0 && (
          <p className="text-sm text-yellow-400">当日无分时数据(可能未开盘或源不通)。</p>
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
