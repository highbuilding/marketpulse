import type { NorthFlowRow } from './types'

export async function fetchNorthFlow(days = 30): Promise<{ rows: NorthFlowRow[] }> {
  const r = await fetch(`/api/north_flow?days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
