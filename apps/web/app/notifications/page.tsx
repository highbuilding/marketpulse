'use client'

import { RecipientManager } from '@/components/RecipientManager'
import { SymbolConfigManager } from '@/components/SymbolConfigManager'

export default function NotificationsPage() {
  return (
    <main className="p-6 max-w-5xl mx-auto space-y-4">
      <header>
        <h1 className="text-2xl font-bold">通知设置</h1>
        <p className="text-sm text-neutral-500 mt-1">
          CD 信号变化(15m / 30m / 1h / 4h / 1d)按市场推送邮件。
          每市场扫完一轮自动比对快照,无变化不发。
        </p>
      </header>
      <RecipientManager />
      <SymbolConfigManager />
    </main>
  )
}
