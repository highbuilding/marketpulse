import type { SectorInfo } from './types'

export async function fetchSectorList(): Promise<{ sectors: SectorInfo[] }> {
  const r = await fetch('/api/sectors/list', { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchSectorConstituents(name: string): Promise<{ sector_name: string; symbols: string[] }> {
  const r = await fetch(`/api/sectors/${encodeURIComponent(name)}/constituents`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
