'use client'

import { useEffect, useMemo, useState, type CSSProperties } from 'react'

import { useMarket } from '@/lib/market-context'
import type { Position } from '@/lib/types'
import { closePosition, listPositions, upsertPosition } from '@/lib/positions_api'

type FormState = {
  symbol: string
  name: string
  quantity: string
  cost_price: string
  strategy_tag: string
  entry_reason: string
  note: string
}

const emptyForm: FormState = {
  symbol: '', name: '', quantity: '', cost_price: '',
  strategy_tag: '', entry_reason: '', note: '',
}

function parseNumber(value: string): number | null {
  const t = value.trim()
  if (!t) return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

function fmtMoney(v: number | null): string {
  return v == null ? '--' : v.toFixed(2)
}

/**
 * 手动持仓面板 (不接券商, 手动填入)。仅 A 股。
 * 从 AI 助手页抽出, 供概览页自选下方复用。
 */
export function PositionsPanel() {
  const { market } = useMarket()
  const [positions, setPositions] = useState<Position[]>([])
  const [form, setForm] = useState<FormState>(emptyForm)
  const [includeClosed, setIncludeClosed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isAshare = market === 'ashare'
  const activeCount = useMemo(
    () => positions.filter((p) => p.status !== 'closed').length,
    [positions],
  )

  const refresh = async () => {
    if (!isAshare) { setPositions([]); return }
    setLoading(true); setError(null)
    try {
      const data = await listPositions(market, includeClosed)
      setPositions(data.positions)
    } catch (e) {
      setError(`持仓加载失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void refresh() /* eslint-disable-line react-hooks/exhaustive-deps */ }, [market, includeClosed])

  const submit = async () => {
    if (!isAshare || saving) return
    const symbol = form.symbol.trim().toUpperCase()
    if (!symbol) { setError('请填写股票代码'); return }
    const quantity = parseNumber(form.quantity)
    const cost = parseNumber(form.cost_price)
    setSaving(true); setError(null)
    try {
      await upsertPosition({
        market, symbol, name: form.name.trim() || null,
        quantity: quantity == null ? 0 : Math.trunc(quantity),
        cost_price: cost,
        strategy_tag: form.strategy_tag.trim() || null,
        entry_reason: form.entry_reason.trim() || null,
        note: form.note.trim() || null, status: 'active',
      })
      setForm(emptyForm); await refresh()
    } catch (e) {
      setError(`持仓保存失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const close = async (symbol: string) => {
    if (!isAshare) return
    setError(null)
    try {
      await closePosition(market, symbol); await refresh()
    } catch (e) {
      setError(`清仓失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  if (!isAshare) {
    return (
      <div className="panel">
        <div className="panel-header">📋 持仓</div>
        <div style={{ padding: 24, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
          手动持仓当前仅支持 A 股
        </div>
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="panel-header">
        📋 持仓
        <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>持有 {activeCount}</span>
        <label style={st.checkboxLabel}>
          <input type="checkbox" checked={includeClosed}
            onChange={(e) => setIncludeClosed(e.target.checked)} />
          显示已清仓
        </label>
      </div>

      <div style={{ padding: '12px 18px' }}>
        <div style={st.formGrid}>
          <input style={st.input} placeholder="代码 002415.SZ" value={form.symbol}
            onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
          <input style={st.input} placeholder="名称" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input style={st.input} placeholder="数量" value={form.quantity}
            onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
          <input style={st.input} placeholder="成本价" value={form.cost_price}
            onChange={(e) => setForm({ ...form, cost_price: e.target.value })} />
          <input style={st.input} placeholder="策略标签" value={form.strategy_tag}
            onChange={(e) => setForm({ ...form, strategy_tag: e.target.value })} />
          <input style={st.input} placeholder="买入理由" value={form.entry_reason}
            onChange={(e) => setForm({ ...form, entry_reason: e.target.value })} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
          <button style={st.primaryButton} onClick={submit} disabled={saving}>
            {saving ? '保存中' : '保存持仓'}
          </button>
        </div>
        {error && <div style={st.error}>{error}</div>}
      </div>

      <table className="data-table">
        <thead><tr><th>标的</th><th>数量</th><th>成本</th><th>策略</th><th>状态</th><th></th></tr></thead>
        <tbody>
          {loading && <tr><td colSpan={6} style={st.center}>加载中...</td></tr>}
          {!loading && positions.length === 0 && (
            <tr><td colSpan={6} style={st.center}>暂无持仓,录入关注或持仓标的</td></tr>
          )}
          {!loading && positions.map((p) => (
            <tr key={`${p.market}:${p.symbol}`}>
              <td>
                <span style={{ fontWeight: 600, display: 'block' }}>{p.name || p.symbol}</span>
                <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>{p.symbol}</span>
              </td>
              <td style={{ fontFamily: 'monospace' }}>{p.quantity}</td>
              <td style={{ fontFamily: 'monospace' }}>{fmtMoney(p.cost_price)}</td>
              <td><span style={{ fontSize: 12 }}>{p.strategy_tag || '--'}</span></td>
              <td>
                <span style={p.status === 'closed' ? st.closedBadge : st.activeBadge}>
                  {p.status === 'closed' ? '已清仓' : '持有'}
                </span>
              </td>
              <td>
                {p.status !== 'closed' && (
                  <button style={st.ghostButton} onClick={() => void close(p.symbol)}>清仓</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  checkboxLabel: { display: 'flex', alignItems: 'center', gap: 5, color: 'var(--text2)', fontSize: 12, fontWeight: 400, marginLeft: 'auto' },
  formGrid: { display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 },
  input: { width: '100%', minWidth: 0, background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 9px', fontSize: 12, outline: 'none' },
  primaryButton: { background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6, padding: '8px 16px', cursor: 'pointer', fontSize: 13 },
  ghostButton: { background: 'transparent', color: 'var(--accent)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px', cursor: 'pointer', fontSize: 12 },
  error: { marginTop: 10, color: '#dc2626', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 6, padding: '8px 10px', fontSize: 13 },
  center: { textAlign: 'center', padding: 24, color: 'var(--text3)' },
  activeBadge: { display: 'inline-block', borderRadius: 999, padding: '3px 8px', fontSize: 11, color: 'var(--green)', background: 'rgba(52,211,153,0.12)' },
  closedBadge: { display: 'inline-block', borderRadius: 999, padding: '3px 8px', fontSize: 11, color: 'var(--text3)', background: 'var(--bg3)' },
}
