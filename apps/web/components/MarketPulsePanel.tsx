'use client'

import useSWR from 'swr'

import { IndexCard } from '@/components/IndexCard'
import { fetchAIMarketPacket } from '@/lib/api'
import type { AIMarketPacket } from '@/lib/types'

const ASHARE_INDICES = [
  '000001.SH',
  '399001.SZ',
  '399006.SZ',
  '000300.SH',
  '000905.SH',
  '000852.SH',
  '000688.SH',
  '000016.SH',
]

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function fmtAmount(v: number | null | undefined): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)} 万亿`
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  return v.toFixed(0)
}

function pctClass(v: number | null | undefined): string {
  if (v == null) return 'text-neutral-500'
  if (v > 0) return 'text-red-400'
  if (v < 0) return 'text-green-400'
  return 'text-neutral-300'
}

function breadthTone(packet: AIMarketPacket): string {
  const { up_ratio, down_ratio } = packet.breadth
  if (down_ratio >= 0.65) return '偏弱'
  if (up_ratio >= 0.65) return '偏强'
  return '均衡'
}

export function MarketPulsePanel() {
  const { data, error, isLoading } = useSWR('ai-market-packet', fetchAIMarketPacket, {
    refreshInterval: 60_000,
  })

  return (
    <section className="space-y-3">
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <h2 className="text-sm text-neutral-400">大盘脉搏</h2>
          <p className="mt-1 text-xs text-neutral-500">8 个 A 股核心指数、全市场宽度、成交额与风格强弱。</p>
        </div>
        <span className="text-xs text-neutral-500">自动刷新 60 秒</span>
      </header>

      {isLoading && <div className="rounded border border-neutral-800 bg-neutral-950 p-4 text-sm text-neutral-500">加载中</div>}
      {error && <div className="rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">大盘数据加载失败</div>}

      {data && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.2fr_2fr]">
          <div className="rounded border border-neutral-800 bg-neutral-950 p-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs text-neutral-500">市场宽度</p>
                <p className="mt-1 text-2xl font-semibold text-neutral-100">{breadthTone(data)}</p>
              </div>
              <div>
                <p className="text-xs text-neutral-500">全 A 成交额</p>
                <p className="mt-1 text-2xl font-semibold text-neutral-100">{fmtAmount(data.breadth.total_amount)}</p>
              </div>
              <div>
                <p className="text-xs text-neutral-500">上涨 / 下跌</p>
                <p className="mt-1 text-sm tabular-nums">
                  <span className="text-red-400">{data.breadth.advancers}</span>
                  <span className="mx-1 text-neutral-600">/</span>
                  <span className="text-green-400">{data.breadth.decliners}</span>
                </p>
              </div>
              <div>
                <p className="text-xs text-neutral-500">涨停 / 跌停</p>
                <p className="mt-1 text-sm tabular-nums">
                  <span className="text-red-400">{data.breadth.up_limit}</span>
                  <span className="mx-1 text-neutral-600">/</span>
                  <span className="text-green-400">{data.breadth.down_limit}</span>
                </p>
              </div>
              <div>
                <p className="text-xs text-neutral-500">中证1000相对上证50</p>
                <p className={`mt-1 text-sm font-semibold tabular-nums ${pctClass(data.index_strength.small_vs_large_pct)}`}>
                  {fmtPct(data.index_strength.small_vs_large_pct)}
                </p>
              </div>
              <div>
                <p className="text-xs text-neutral-500">创业板相对上证50</p>
                <p className={`mt-1 text-sm font-semibold tabular-nums ${pctClass(data.index_strength.growth_vs_large_pct)}`}>
                  {fmtPct(data.index_strength.growth_vs_large_pct)}
                </p>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {ASHARE_INDICES.map((symbol) => <IndexCard key={symbol} symbol={symbol} />)}
          </div>
        </div>
      )}
    </section>
  )
}
