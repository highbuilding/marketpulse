'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import useSWR from 'swr'
import { listCDSignals } from '@/lib/cd_signals_api'
import { inferMarket, type Market } from '@/lib/markets'
import { fmtSignalTs } from '@/lib/signal_time'
import type { CDSignalDTO, AnySignalInterval } from '@/lib/types'

type MarketFilter = 'all' | Market
const MARKET_CHIPS: { id: MarketFilter; label: string }[] = [
  { id: 'all', label: '全部市场' },
  { id: 'ashare', label: 'A股' },
  { id: 'us', label: '美股' },
  { id: 'crypto', label: 'Crypto' },
]
const INTERVAL_OPTS = ['全部', '1d', '4h', '60m', '30m', '15m']

export default function SignalsPage() {
  const router = useRouter()
  const [market, setMarket] = useState<MarketFilter>('all')
  const [dirs, setDirs] = useState<Array<'buy' | 'sell'>>(['buy', 'sell'])
  const [interval, setIntervalSel] = useState('全部')

  const { data, isLoading, error } = useSWR(
    'cd-signals:page',
    () => listCDSignals({ limit: 200 }),
    { refreshInterval: 60_000 },
  )
  const all: CDSignalDTO[] = data?.signals ?? []

  const toggleDir = (d: 'buy' | 'sell') =>
    setDirs((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d]))

  const rows = all
    .map((s) => ({ ...s, market: inferMarket(s.symbol) }))
    .filter((s) =>
      (market === 'all' || s.market === market) &&
      dirs.includes(s.signal_type) &&
      (interval === '全部' || s.interval === interval)
    )

  const chip = (active: boolean): React.CSSProperties => ({
    fontSize: 12, padding: '3px 12px', borderRadius: 14, cursor: 'pointer',
    border: '1px solid ' + (active ? 'transparent' : 'var(--border)'),
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#fff' : 'var(--text2)',
  })

  return (
    <div style={{ padding: 24, maxWidth: 920, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>🎯 CD 信号</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginBottom: 16 }}>
        基于富途 CD 指标的多周期顶底背离扫描,按检测时间倒序。点任意一行查看该标的详情。
      </p>

      {/* 筛选条 */}
      <div className="panel" style={{ padding: '12px 14px', marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {MARKET_CHIPS.map((m) => (
          <span key={m.id} style={chip(market === m.id)} onClick={() => setMarket(m.id)}>{m.label}</span>
        ))}
        <span style={{ width: 1, height: 18, background: 'var(--border)' }} />
        <span style={chip(dirs.includes('buy'))} onClick={() => toggleDir('buy')}>买入</span>
        <span style={chip(dirs.includes('sell'))} onClick={() => toggleDir('sell')}>卖出</span>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text2)' }}>周期：</span>
        <select value={interval} onChange={(e) => setIntervalSel(e.target.value)}
          style={{ background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 5, padding: '3px 8px', fontSize: 12 }}>
          {INTERVAL_OPTS.map((iv) => <option key={iv} value={iv}>{iv}</option>)}
        </select>
      </div>

      {/* 事件流 */}
      <div className="panel">
        {isLoading && <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>加载中…</div>}
        {error && <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>信号加载失败,稍后重试</div>}
        {!isLoading && !error && rows.length === 0 && (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>当前筛选无信号</div>
        )}
        {rows.map((s) => (
          <div key={s.id} onClick={() => router.push(`/symbol/${encodeURIComponent(s.symbol)}`)}
            style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px', borderBottom: '1px solid rgba(128,128,128,0.08)', cursor: 'pointer' }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg3)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
            <span className={`sig-badge ${s.signal_type}`} style={{ width: 32, height: 32, fontSize: 14 }}>
              {s.signal_type === 'buy' ? '▲' : '▼'}
            </span>
            <span style={{ color: s.signal_type === 'buy' ? 'var(--red)' : 'var(--green)', fontSize: 12, fontWeight: 600, width: 28 }}>
              {s.signal_type === 'buy' ? '买入' : '卖出'}
            </span>
            <b className="font-mono" style={{ minWidth: 110 }}>{s.symbol}</b>
            <span style={{ color: 'var(--text3)', fontSize: 12 }}>{s.interval}</span>
            <span style={{ color: 'var(--text2)', fontSize: 12 }}>{s.signal_type === 'buy' ? '底背离' : '顶背离'}</span>
            <span className="font-mono" style={{ marginLeft: 'auto', color: 'var(--text3)', fontSize: 12 }}>
              {fmtSignalTs(s.bar_ts, s.interval as AnySignalInterval, s.market)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
