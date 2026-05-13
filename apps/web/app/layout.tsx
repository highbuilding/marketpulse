import './globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'MarketPulse',
  description: '四市场行情监控',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">{children}</body>
    </html>
  )
}
