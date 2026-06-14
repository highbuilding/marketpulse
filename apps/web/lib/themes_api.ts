import { apiFetch } from './api_fetch'
import type { Market, ThemeClassification, ThemeConstituent, ThemeDefinition, ThemePriority } from './types'

export interface ThemeInput {
  market: Market
  theme_code?: string | null
  theme_name: string
  classification: ThemeClassification
  priority: ThemePriority
  enabled?: boolean
  note?: string | null
}

export interface ThemePatch {
  theme_name?: string
  classification?: ThemeClassification
  priority?: ThemePriority
  enabled?: boolean
  note?: string | null
}

export interface ConstituentInput {
  symbol: string
  name?: string | null
  role_hint?: string | null
  weight?: number | null
  enabled?: boolean
  note?: string | null
}

export async function listThemes(
  market: Market,
  includeDisabled = true,
): Promise<{ themes: ThemeDefinition[] }> {
  const params = new URLSearchParams({ market, include_disabled: String(includeDisabled) })
  const r = await apiFetch(`/api/themes?${params.toString()}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function getTheme(
  market: Market,
  themeCode: string,
): Promise<{ theme: ThemeDefinition; constituents: ThemeConstituent[] }> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/themes/${encodeURIComponent(themeCode)}?${params.toString()}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function createTheme(input: ThemeInput): Promise<{ theme_code: string }> {
  const r = await apiFetch('/api/themes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function patchTheme(
  market: Market,
  themeCode: string,
  patch: ThemePatch,
): Promise<{ theme_code: string }> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/themes/${encodeURIComponent(themeCode)}?${params.toString()}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function deleteTheme(market: Market, themeCode: string): Promise<void> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/themes/${encodeURIComponent(themeCode)}?${params.toString()}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function upsertConstituent(
  market: Market,
  themeCode: string,
  input: ConstituentInput,
): Promise<{ symbol: string }> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/themes/${encodeURIComponent(themeCode)}/constituents?${params.toString()}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function deleteConstituent(
  market: Market,
  themeCode: string,
  symbol: string,
): Promise<void> {
  const params = new URLSearchParams({ market })
  const r = await apiFetch(`/api/themes/${encodeURIComponent(themeCode)}/constituents/${encodeURIComponent(symbol)}?${params.toString()}`, {
    method: 'DELETE',
  })
  if (!r.ok) throw new Error(`${r.status}`)
}
