'use client'

import type { AdapterHealth } from '@/lib/types'
import clsx from 'clsx'

const COLORS: Record<AdapterHealth['state'], string> = {
  ok: 'bg-green-600 text-white',
  degraded: 'bg-yellow-600 text-white',
  disabled: 'bg-neutral-600 text-neutral-200',
  down: 'bg-red-600 text-white',
}

export function HealthBadge({ health }: { health: AdapterHealth | undefined }) {
  if (!health) return null
  return (
    <span
      className={clsx('px-2 py-0.5 rounded text-xs font-medium', COLORS[health.state])}
      title={health.detail ?? undefined}
    >
      {health.state}
    </span>
  )
}
