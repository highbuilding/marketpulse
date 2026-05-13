import type { HealthResponse, Market, OverviewResponse } from './types'

async function j<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${path} -> ${res.status}`)
  return res.json() as Promise<T>
}

export const fetchHealth = () => j<HealthResponse>('/api/health')
export const fetchOverview = (m: Market) => j<OverviewResponse>(`/api/markets/${m}/overview`)
