'use client'

import useSWR from 'swr'
import clsx from 'clsx'

interface Sector {
  name: string
  change_pct: number
  avg_price: number
  company_count: number
  leader_name: string
  leader_change_pct: number
}

interface SectorsResponse {
  market: string
  sectors: Sector[]
}

async function fetchSectors(): Promise<SectorsResponse> {
  const r = await fetch('/api/markets/ashare/sectors', { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

function bgFor(pct: number): string {
  if (pct >= 5) return 'bg-red-700'
  if (pct >= 3) return 'bg-red-600'
  if (pct >= 1) return 'bg-red-500'
  if (pct >= 0) return 'bg-red-900/60'
  if (pct >= -1) return 'bg-green-900/60'
  if (pct >= -3) return 'bg-green-600'
  if (pct >= -5) return 'bg-green-700'
  return 'bg-green-800'
}

export function SectorHeatmap() {
  const { data, error, isLoading } = useSWR('sectors:ashare', fetchSectors, {
    refreshInterval: 60_000,
  })

  return (
    <section className="rounded-lg border border-neutral-800 p-4 bg-neutral-950">
      <h2 className="text-lg font-semibold mb-3">A 股 行业热力图(新浪行业)</h2>
      {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {error && <p className="text-sm text-red-400">加载失败</p>}
      {data && (
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-1.5">
          {data.sectors
            .slice()
            .sort((a, b) => b.change_pct - a.change_pct)
            .map((s) => (
              <div
                key={s.name}
                className={clsx(
                  'rounded p-2 text-white text-xs flex flex-col justify-between',
                  bgFor(s.change_pct),
                )}
                title={`领涨:${s.leader_name} ${s.leader_change_pct.toFixed(2)}% / 公司家数:${s.company_count}`}
              >
                <div className="font-medium truncate">{s.name}</div>
                <div className="tabular-nums font-mono">
                  {s.change_pct >= 0 ? '+' : ''}{s.change_pct.toFixed(2)}%
                </div>
              </div>
            ))}
        </div>
      )}
    </section>
  )
}
