'use client'

import './globals.css'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState, useEffect } from 'react'
import { MarketProvider, useMarket, MARKET_LABELS, type MarketId } from '@/lib/market-context'
import { marketPhase, marketPhaseLabel, type MarketPhase } from '@/lib/markets'
import { fetchHealth } from '@/lib/api'
import useSWR from 'swr'

const NAV_ITEMS = [
  { id: 'dashboard', label: '概览',     icon: '📊', href: '/' },
  { id: 'market',    label: '行情',     icon: '📈', href: '/market' },
  { id: 'watchlist', label: '自选',     icon: '⭐', href: '/watchlist' },
  { id: 'signals',   label: 'CD 信号',  icon: '🎯', href: '/signals' },
  { id: 'strategy',  label: '策略回测', icon: '🧪', href: '/strategy' },
  { id: 'assistant', label: 'AI 助手',  icon: '🤖', href: '/assistant' },
  { id: 'trading',   label: '自动交易', icon: '⚡', href: '/trading' },
]

function ClientTime() {
  const [t, setT] = useState('')
  useEffect(() => {
    setT(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }))
    const id = setInterval(() => setT(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })), 1000)
    return () => clearInterval(id)
  }, [])
  return <span style={{ fontSize: 12, color: 'var(--text3)', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{t || '--:--:--'} BJT</span>
}

const PHASE_COLOR: Record<MarketPhase, string> = {
  open: 'var(--green)', pre: '#e0a73e', after: '#e0a73e', lunch: '#e0a73e', closed: 'var(--text3)',
}

function MarketPhaseBadge({ market }: { market: MarketId }) {
  const [phase, setPhase] = useState<MarketPhase | null>(null)
  useEffect(() => {
    const tick = () => setPhase(marketPhase(market))
    tick()
    const id = setInterval(tick, 30_000)
    return () => clearInterval(id)
  }, [market])

  // 数据源健康: crypto 市场对应 adapter 名是 binance, 其余同名
  const { data: health } = useSWR('health', fetchHealth, { refreshInterval: 30_000 })
  const adapterKey = market === 'crypto' ? 'binance' : market
  const adapter = health?.adapters?.[adapterKey as MarketId]
  const offline = adapter && (adapter.state === 'down' || adapter.state === 'degraded')

  if (offline) {
    return (
      <span style={{ fontSize: 12, color: 'var(--red)', whiteSpace: 'nowrap' }}
        title={adapter?.detail ?? '数据源不可用'}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--red)', display: 'inline-block', marginRight: 4 }} />
        数据源离线
      </span>
    )
  }
  if (!phase) return <span style={{ fontSize: 12, color: 'var(--text3)', whiteSpace: 'nowrap' }}>--</span>
  return (
    <span style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: PHASE_COLOR[phase], display: 'inline-block', marginRight: 4 }} />
      {marketPhaseLabel(phase)}
    </span>
  )
}

function Sidebar() {
  const pathname = usePathname()
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  const activeNav = pathname === '/' ? 'dashboard'
    : pathname.startsWith('/market') ? 'market'
    : pathname.startsWith('/watchlist') ? 'watchlist'
    : pathname.startsWith('/signals') ? 'signals'
    : pathname.startsWith('/notifications') ? 'signals'
    : pathname.startsWith('/strategy') ? 'strategy'
    : pathname.startsWith('/assistant') ? 'assistant'
    : pathname.startsWith('/trading') ? 'trading'
    : pathname.startsWith('/settings') ? 'settings'
    : pathname.startsWith('/ai-market') ? 'market'
    : 'market'

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  return (
    <aside className="sidebar">
      <Link href="/" className="sidebar-logo" style={{ color: 'inherit', textDecoration: 'none' }}>
        Market<span>Pulse</span>
      </Link>
      <div className="sidebar-nav-label">导航</div>
      {NAV_ITEMS.map(item => (
        <Link key={item.id} href={item.href}
          className={`sidebar-item ${activeNav === item.id ? 'active' : ''}`}
          style={{ color: 'inherit', textDecoration: 'none' }}
        >
          <span>{item.icon}</span> {item.label}
        </Link>
      ))}
      <div className="sidebar-nav-label">系统</div>
      <Link href="/settings"
        className={`sidebar-item ${activeNav === 'settings' ? 'active' : ''}`}
        style={{ color: 'inherit', textDecoration: 'none' }}
      >
        <span>⚙️</span> 设置
      </Link>
      <div className="sidebar-footer">
        <div className={`theme-toggle ${theme === 'light' ? 'light' : ''}`}
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} />
        <span style={{ color: 'var(--text3)', whiteSpace: 'nowrap' }}>主题</span>
      </div>
    </aside>
  )
}

function TopBar() {
  const { market, setMarket, marketLabel } = useMarket()
  const router = useRouter()

  return (
    <header className="topbar">
      <MarketSwitcher market={market} setMarket={setMarket} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <input type="text" placeholder="⌘K 搜索代码..."
          style={{ width: 180 }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const v = (e.target as HTMLInputElement).value.trim()
              if (v) router.push(`/symbol/${encodeURIComponent(v)}`)
            }
          }}
        />
        <MarketPhaseBadge market={market} />
        <ClientTime />
        <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white', fontWeight: 600, fontSize: 11 }}>Z</div>
      </div>
    </header>
  )
}

function MarketSwitcher({ market, setMarket }: { market: MarketId; setMarket: (m: MarketId) => void }) {
  const [open, setOpen] = useState(false)
  const cur = MARKET_LABELS[market]
  return (
    <div style={{ position: 'relative' }}>
      <button className="mkt-tab active" onClick={() => setOpen((o) => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span>{cur.flag} {cur.name}</span>
        <span style={{ fontSize: 10, color: 'var(--text3)' }}>▾</span>
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 10 }} />
          <div style={{ position: 'absolute', top: '110%', left: 0, zIndex: 11,
            background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8,
            padding: 4, minWidth: 160, boxShadow: '0 6px 24px rgba(0,0,0,0.3)' }}>
            {(Object.entries(MARKET_LABELS) as [MarketId, typeof MARKET_LABELS['ashare']][]).map(([id, info]) => (
              <button key={id} onClick={() => { setMarket(id); setOpen(false) }}
                className={`sidebar-item ${market === id ? 'active' : ''}`}
                style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', border: 'none', cursor: 'pointer' }}>
                <span>{info.flag} {info.name}</span>
                <span style={{ fontSize: 10, color: 'var(--text3)' }}>{info.sub}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// 按市场隔离内容: key=market 使切换市场时整棵子树重挂载,
// 旧市场组件卸载 → 其 SSE EventSource 在 cleanup 里 close, 不再保留多市场长连接。
function MarketScopedContent({ children }: { children: React.ReactNode }) {
  const { market } = useMarket()
  return <div key={market}>{children}</div>
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" data-theme="dark">
      <body className="flex h-screen overflow-hidden">
        <MarketProvider>
          <Sidebar />
          <main className="flex-1 flex flex-col overflow-hidden">
            <TopBar />
            <div className="flex-1 overflow-y-auto">
              <MarketScopedContent>{children}</MarketScopedContent>
            </div>
          </main>
        </MarketProvider>
      </body>
    </html>
  )
}
