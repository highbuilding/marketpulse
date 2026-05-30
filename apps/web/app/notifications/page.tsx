'use client'

import { Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import { RecipientManager } from '@/components/RecipientManager'
import { SymbolConfigManager } from '@/components/SymbolConfigManager'
import useSWR from 'swr'
import { listCDSignals } from '@/lib/cd_signals_api'
import Link from 'next/link'

const MARKET_LABELS: Record<string, string> = {
  ashare: 'A 股', us: '美股', crypto: 'Crypto', hk: '港股',
}

function SignalsContent() {
  const searchParams = useSearchParams()
  const market = searchParams.get('market') || undefined
  const marketLabel = market ? MARKET_LABELS[market] ?? market : '全部市场'
  const swrKey = market ? `signals:page:${market}` : 'signals:page'
  const { data: signalsResp } = useSWR(swrKey,
    () => listCDSignals({ market, limit: 200 }), { refreshInterval: 60_000 })
  const signals = signalsResp?.signals ?? []

  return (
    <div style={{ padding: 20 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>🎯 CD 信号 · {marketLabel}</h1>
        <p style={{ color: 'var(--text2)' }}>基于富途 CD 指标的多周期顶底背离扫描</p>
      </div>

      {/* Signal list */}
      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-header">
          最新信号
          <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{signals.length} 条</span>
        </div>
        {signals.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>
            暂无信号，等待 CD 扫描...
          </div>
        ) : (
          signals.map((s: any, i: number) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 18px', borderBottom: '1px solid var(--border)' }}>
              <div className={`sig-badge ${s.signal_type === 'buy' ? 'buy' : 'sell'}`}>
                {s.signal_type === 'buy' ? '📈' : '📉'}
              </div>
              <div style={{ flex: 1 }}>
                <Link href={`/symbol/${encodeURIComponent(s.symbol)}`} style={{ fontWeight: 600, color: 'inherit', textDecoration: 'none' }}>
                  {s.symbol}
                </Link>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                  {s.signal_type === 'buy' ? '底背离买入' : '顶背离卖出'} · {s.interval}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{s.bar_ts}</div>
              </div>
              <span className="sig-mkt">{s.market || '—'}</span>
            </div>
          ))
        )}
      </div>

      {/* Settings */}
      <div className="panel">
        <div className="panel-header">⚙️ 通知设置</div>
        <div style={{ padding: 18 }}>
          <p style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16 }}>
            CD 信号变化(15m / 30m / 1h / 4h / 1d)按市场推送。
          </p>
          <RecipientManager />
          <div style={{ marginTop: 20 }}>
            <SymbolConfigManager />
          </div>
        </div>
      </div>
    </div>
  )
}

export default function NotificationsPage() {
  return (
    <Suspense fallback={<div style={{ padding: 20, color: 'var(--text3)' }}>加载中...</div>}>
      <SignalsContent />
    </Suspense>
  )
}
