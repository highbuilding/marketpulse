'use client'

import { useRouter } from 'next/navigation'
import { useMemo, useState } from 'react'
import useSWR from 'swr'

import { IntradayChart } from '@/components/IntradayChart'
import { KLineChart, type SignalMarker } from '@/components/KLineChart'
import { FundFlowPanel } from '@/components/FundFlowPanel'
import { CDSignalPanel } from '@/components/CDSignalPanel'
import { fetchBars, fetchSymbolProfile } from '@/lib/symbol_api'
import { listCDSignalsBySymbol } from '@/lib/cd_signals_api'
import { CD_MARKER_INTERVALS, klineTabsForMarket } from '@/lib/intervals'
import { inferMarket } from '@/lib/markets'
import type { Interval } from '@/lib/types'

// A 股交易时段(北京时间):09:30–11:30 / 13:00–15:00,周一至周五
function isAshareTradingNow(): boolean {
  const now = new Date()
  const beijing = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }))
  const day = beijing.getDay()
  if (day === 0 || day === 6) return false
  const minutes = beijing.getHours() * 60 + beijing.getMinutes()
  return (minutes >= 9 * 60 + 30 && minutes <= 11 * 60 + 30)
    || (minutes >= 13 * 60 && minutes <= 15 * 60)
}

export default function SymbolPage({ params }: { params: { code: string } }) {
  const symbol = decodeURIComponent(params.code)
  const [interval, setInterval] = useState<Interval>('1m')
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
    () => klineTabsForMarket(profile?.market ?? null),
    [profile?.market],
  )

  const isIntraday = ['1m', '5m', '15m', '30m', '60m'].includes(interval)
  // 日/周/月线 + 4h:从 2020-01-01 至今,确保至少覆盖 6 年(60m 重采样源也要跨足够长的窗口)
  const daysSinceY2020 = Math.ceil((Date.now() - new Date('2020-01-01').getTime()) / 86_400_000)
  const days = interval === '1m' ? 1 : isIntraday ? 5 : daysSinceY2020

  const { data, error, isLoading } = useSWR(
    `bars:${symbol}:${interval}:${days}`,
    () => fetchBars(symbol, interval, days),
    {
      refreshInterval: () => {
        if (interval === '1m') return isAshareTradingNow() ? 10_000 : 0
        if (isIntraday) return 60_000
        return 0
      },
      revalidateOnFocus: interval === '1m',
    },
  )

  // CD 信号(只对 60m/4h/1d 有意义),用于在 KLineChart 上叠 markers
  const signalInterval = CD_MARKER_INTERVALS.has(interval) ? interval : null
  const { data: signalsResp } = useSWR(
    signalInterval ? `cd:${symbol}:${signalInterval}` : null,
    () => listCDSignalsBySymbol(symbol, signalInterval ? [signalInterval] : undefined),
    { refreshInterval: 60_000 },
  )
  const markers: SignalMarker[] = useMemo(() => {
    if (!signalsResp || !data || data.bars.length === 0) return []
    // 过滤超出 bars 时间窗口的历史 marker, 否则 lightweight-charts 会把越界 marker
    // 全部堆到最左边那根 bar 上 (intraday 视图 days=5, 但信号是全量历史的)
    const minTs = new Date(data.bars[0].ts).getTime()
    const maxTs = new Date(data.bars[data.bars.length - 1].ts).getTime()
    return signalsResp.signals
      .filter((s) => {
        const t = new Date(s.bar_ts).getTime()
        return t >= minTs && t <= maxTs
      })
      .map((s) => ({ ts: s.bar_ts, signal_type: s.signal_type }))
  }, [signalsResp, data])

  // 分时模式:拉日线最后一根作 prevClose,只展示当日
  const { data: daily } = useSWR(
    interval === '1m' ? `bars:${symbol}:1d:5` : null,
    () => fetchBars(symbol, '1d', 5),
  )
  const todayBars = useMemo(() => {
    if (!data || interval !== '1m' || data.bars.length === 0) return data?.bars ?? []
    // 取最后一根 bar 的北京时间日期,过滤同一交易日
    const lastBar = data.bars[data.bars.length - 1]
    const lastDate = new Date(lastBar.ts).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
    return data.bars.filter((b) => {
      const bDate = new Date(b.ts).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
      return bDate === lastDate
    })
  }, [data, interval])

  const prevClose = useMemo(() => {
    if (!daily || daily.bars.length < 2) return null
    // 倒数第二根日线作昨收(最后一根可能就是今日实时不准)
    return daily.bars[daily.bars.length - 2]?.close ?? daily.bars[daily.bars.length - 1]?.close
  }, [daily])

  // 当前价位指针(K 线模式用): 拉 1m 分时, 取最末根 close 作 livePrice
  // 在 5m/15m/30m/60m/4h K 线上画一条水平虚线, 让用户即使当前 bar 没收盘也能看到价位
  const isKlineMode = interval !== '1m' && interval !== '1d' && interval !== '1wk' && interval !== '1mo'
  const { data: live1m } = useSWR(
    isKlineMode ? `bars:${symbol}:1m:1` : null,
    () => fetchBars(symbol, '1m', 1),
    { refreshInterval: 30_000 },
  )
  const livePrice: number | null = useMemo(() => {
    if (!live1m || live1m.bars.length === 0) return null
    return live1m.bars[live1m.bars.length - 1].close
  }, [live1m])

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
        {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
        {error && <p className="text-sm text-red-400">加载失败:{String(error)}</p>}

        {/* 分时模式 */}
        {interval === '1m' && data && todayBars.length > 0 && (
          <IntradayChart
            bars={todayBars}
            prevClose={prevClose}
            height={420}
            market={effectiveMarket}
          />
        )}
        {interval === '1m' && data && todayBars.length === 0 && (
          <p className="text-sm text-yellow-400">当日无分时数据(可能未开盘或源不通)。</p>
        )}

        {/* 其他周期:K 线 */}
        {interval !== '1m' && data && data.bars.length > 0 && (
          <KLineChart
            bars={data.bars}
            interval={interval}
            market={effectiveMarket}
            height={420}
            signals={signalInterval ? markers : undefined}
            livePrice={isKlineMode ? livePrice : null}
          />
        )}
        {interval !== '1m' && data && data.bars.length === 0 && (
          <p className="text-sm text-yellow-400">无数据。请先 <code>make warmup</code> 或本周期还未抓取。</p>
        )}
      </section>

      {!isIndex && <FundFlowPanel symbol={symbol} />}
      <CDSignalPanel symbol={symbol} market={effectiveMarket} />
    </main>
  )
}
