export type Market = 'ashare' | 'us' | 'hk' | 'crypto'
export type Channel = 'email' | 'wechat'
export type SignalInterval = '15m' | '30m' | '60m' | '4h' | '1d'

export interface Recipient {
  id: number
  market: Market
  channel: Channel
  address: string
  enabled: boolean
}

export interface SymbolConfig {
  symbol: string
  intervals: SignalInterval[]
}

export async function listRecipients(market?: Market): Promise<{ recipients: Recipient[] }> {
  const q = market ? `?market=${market}` : ''
  const r = await fetch(`/api/notifications/recipients${q}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function addRecipient(market: Market, channel: Channel, address: string): Promise<{ id: number }> {
  const r = await fetch('/api/notifications/recipients', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, channel, address }),
  })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`${r.status} ${text}`)
  }
  return r.json()
}

export async function setRecipientEnabled(id: number, enabled: boolean): Promise<void> {
  const r = await fetch(`/api/notifications/recipients/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function deleteRecipient(id: number): Promise<void> {
  const r = await fetch(`/api/notifications/recipients/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function listSymbolConfigs(): Promise<{ configs: SymbolConfig[] }> {
  const r = await fetch('/api/notifications/symbol-config', { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function upsertSymbolConfig(symbol: string, intervals: SignalInterval[]): Promise<void> {
  const r = await fetch(`/api/notifications/symbol-config/${encodeURIComponent(symbol)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ intervals }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function deleteSymbolConfig(symbol: string): Promise<void> {
  const r = await fetch(`/api/notifications/symbol-config/${encodeURIComponent(symbol)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function sendTestEmail(market: Market): Promise<{ ok: boolean; sent_to: number; error: string | null }> {
  const r = await fetch(`/api/notifications/test?market=${market}`, { method: 'POST' })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`${r.status} ${text}`)
  }
  return r.json()
}
