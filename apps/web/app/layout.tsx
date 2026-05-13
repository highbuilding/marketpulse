import './globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'MarketPulse',
  description: '四市场行情监控',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">
        <nav className="border-b border-neutral-800 bg-neutral-950 px-6 py-3 flex gap-6 text-sm">
          <a href="/dashboard" className="font-bold">MarketPulse</a>
          <a href="/dashboard" className="text-neutral-400 hover:text-neutral-200">Dashboard</a>
          <a href="/watchlist" className="text-neutral-400 hover:text-neutral-200">我的关注</a>
        </nav>
        {children}
      </body>
    </html>
  )
}
