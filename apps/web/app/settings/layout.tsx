'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

const TABS = [
  { href: '/settings/notifications', label: '信号通知' },
  { href: '/settings/themes',        label: '题材库' },
  { href: '/settings/preferences',   label: '偏好' },
  { href: '/settings/about',         label: '关于' },
]

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  return (
    <div style={{ padding: 24, maxWidth: 1180, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, marginBottom: 14 }}>⚙️ 设置</h1>
      <div style={{ display: 'flex', gap: 6, marginBottom: 18, borderBottom: '1px solid var(--border)' }}>
        {TABS.map((t) => {
          const active = pathname === t.href
          return (
            <Link key={t.href} href={t.href}
              style={{
                padding: '8px 16px', fontSize: 13, textDecoration: 'none',
                color: active ? 'var(--accent)' : 'var(--text2)',
                borderBottom: '2px solid ' + (active ? 'var(--accent)' : 'transparent'),
                marginBottom: -1,
              }}>
              {t.label}
            </Link>
          )
        })}
      </div>
      {children}
    </div>
  )
}
