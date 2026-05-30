'use client'

import './globals.css'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState, useEffect } from 'react'
import { MarketProvider, useMarket, MARKET_LABELS, type MarketId } from '@/lib/market-context'

const NAV_ITEMS = [
  { id: 'dashboard', label: '大盘', icon: '📊', href: '/' },
  { id: 'market',   label: '行情', icon: '📈', href: '/market' },
  { id: 'watchlist',label: '自选', icon: '⭐', href: '/watchlist' },
  { id: 'signals',  label: '信号', icon: '🎯', href: '/notifications' },
  { id: 'ai',       label: 'AI',   icon: '🤖', href: '/ai-market' },
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

function Sidebar() {
  const pathname = usePathname()
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  const activeNav = pathname === '/' ? 'dashboard'
    : pathname.startsWith('/market') ? 'market'
    : pathname.startsWith('/watchlist') ? 'watchlist'
    : pathname.startsWith('/notifications') ? 'signals'
    : pathname.startsWith('/ai-market') ? 'ai'
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
      <Link href="/notifications"
        className={`sidebar-item`}
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
      <div className="market-tabs">
        {(Object.entries(MARKET_LABELS) as [MarketId, typeof MARKET_LABELS['ashare']][]).map(([id, info]) => (
          <button key={id} className={`mkt-tab ${market === id ? 'active' : ''}`}
            onClick={() => setMarket(id)}>
            {info.flag} {info.name}
            <span className="sub">{info.sub}</span>
          </button>
        ))}
      </div>
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
        <span style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--green)', display: 'inline-block', marginRight: 4 }} />
          实时
        </span>
        <ClientTime />
        <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white', fontWeight: 600, fontSize: 11 }}>Z</div>
      </div>
    </header>
  )
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
              {children}
            </div>
          </main>
        </MarketProvider>
      </body>
    </html>
  )
}
