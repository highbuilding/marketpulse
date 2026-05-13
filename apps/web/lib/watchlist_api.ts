import type { Watchlist } from './types'

export async function listWatchlists(): Promise<{ watchlists: Watchlist[] }> {
  const r = await fetch('/api/watchlists', { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function createWatchlist(name: string): Promise<{ id: number }> {
  const r = await fetch('/api/watchlists', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function renameWatchlist(id: number, name: string): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function archiveWatchlist(id: number): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function listWatchlistSymbols(id: number): Promise<{ symbols: string[] }> {
  const r = await fetch(`/api/watchlists/${id}/symbols`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function addWatchlistSymbol(id: number, symbol: string): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}/symbols`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function removeWatchlistSymbol(id: number, symbol: string): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}/symbols/${encodeURIComponent(symbol)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}
