'use client'

import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import Link from 'next/link'

import { useMarket } from '@/lib/market-context'
import type { Position } from '@/lib/types'
import {
  createPosition, listPositions, patchPosition, closePosition, deletePosition,
} from '@/lib/positions_api'

type FormState = {
  symbol: string; name: string; quantity: string; cost_price: string
  opened_at: string; strategy_tag: string; entry_reason: string; note: string
}

const emptyForm: FormState = {
  symbol: '', name: '', quantity: '', cost_price: '',
  opened_at: '', strategy_tag: '', entry_reason: '', note: '',
}

function numOrNull(v: string): number | null {
  const t = v.trim(); if (!t) return null
  const n = Number(t); return Number.isFinite(n) ? n : null
}
function fmt(v: number | null): string { return v == null ? '--' : v.toFixed(2) }
function fmtDate(v: string | null): string {
  if (!v) return '--'
  const d = new Date(v); return Number.isNaN(d.getTime()) ? '--' : d.toLocaleDateString('zh-CN')
}

export default function PositionsPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const [positions, setPositions] = useState<Position[]>([])
  const [form, setForm] = useState<FormState>(emptyForm)
  const [includeClosed, setIncludeClosed] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    if (!isAshare) { setPositions([]); return }
    try {
      const d = await listPositions(market, includeClosed)
      setPositions(d.positions)
    } catch (e) {
      setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }
  useEffect(() => { void refresh() /* eslint-disable-line */ }, [market, includeClosed])

  const active = useMemo(() => positions.filter((p) => p.status !== 'closed'), [positions])
  const closed = useMemo(() => positions.filter((p) => p.status === 'closed'), [positions])

  const submit = async () => {
    if (!isAshare || saving) return
    const symbol = form.symbol.trim().toUpperCase()
    if (!symbol) { setError('请填写代码'); return }
    setSaving(true); setError(null)
    try {
      await createPosition({
        market, symbol,
        name: form.name.trim() || null,
        quantity: numOrNull(form.quantity) ?? 0,
        cost_price: numOrNull(form.cost_price),
        opened_at: form.opened_at.trim() || null,
        strategy_tag: form.strategy_tag.trim() || null,
        entry_reason: form.entry_reason.trim() || null,
        note: form.note.trim() || null,
      })
      setForm(emptyForm); await refresh()
    } catch (e) {
      setError(`保存失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally { setSaving(false) }
  }

  const doClose = async (p: Position) => {
    const input = window.prompt(`平仓 ${p.name || p.symbol} — 填平仓价(开仓价 ${fmt(p.cost_price)}):`, '')
    if (input == null) return
    const cp = numOrNull(input)
    try { await closePosition(p.id, cp); await refresh() }
    catch (e) { setError(`平仓失败: ${e instanceof Error ? e.message : String(e)}`) }
  }

  const doDelete = async (p: Position) => {
    if (!window.confirm(`删除 ${p.name || p.symbol} 这条记录?不可恢复`)) return
    try { await deletePosition(p.id); await refresh() }
    catch (e) { setError(`删除失败: ${e instanceof Error ? e.message : String(e)}`) }
  }

  if (!isAshare) {
    return (
      <div style={st.page}>
        <h1 style={st.h1}>📋 持仓管理</h1>
        <p style={st.sub}>{marketLabel.name}暂不支持持仓,当前仅 A 股。</p>
      </div>
    )
  }

  return (
    <div style={st.page}>
      <h1 style={st.h1}>📋 持仓管理</h1>
      <p style={st.sub}>手动记录开仓/平仓(不接券商、不取实时价)。盈亏 = (平仓价−开仓价)×股数。</p>

      {/* 录入表单 */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">新增持仓</div>
        <div style={{ padding: '14px 18px' }}>
          <div style={st.formGrid}>
            <input style={st.input} placeholder="代码 002415.SZ" value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
            <input style={st.input} placeholder="名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input style={st.input} placeholder="股数" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
            <input style={st.input} placeholder="开仓价" value={form.cost_price} onChange={(e) => setForm({ ...form, cost_price: e.target.value })} />
            <input style={st.input} placeholder="开仓日期 2026-06-08" value={form.opened_at} onChange={(e) => setForm({ ...form, opened_at: e.target.value })} />
            <input style={st.input} placeholder="策略标签" value={form.strategy_tag} onChange={(e) => setForm({ ...form, strategy_tag: e.target.value })} />
            <input style={{ ...st.input, gridColumn: '1 / -1' }} placeholder="买入理由 / 备注" value={form.entry_reason} onChange={(e) => setForm({ ...form, entry_reason: e.target.value })} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
            <button style={st.btn} onClick={submit} disabled={saving}>{saving ? '保存中' : '新增持仓'}</button>
          </div>
          {error && <div style={st.err}>{error}</div>}
        </div>
      </div>

      {/* 活跃持仓 */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-header">持有中<span style={st.count}>{active.length}</span></div>
        <table className="data-table">
          <thead><tr><th>标的</th><th>股数</th><th>开仓价</th><th>开仓日</th><th>策略</th><th></th></tr></thead>
          <tbody>
            {active.length === 0 && <tr><td colSpan={6} style={st.empty}>暂无持有</td></tr>}
            {active.map((p) => (
              <tr key={p.id}>
                <td><span style={{ fontWeight: 500, display: 'block' }}>{p.name || p.symbol}</span>
                  <span style={st.code}>{p.symbol}</span></td>
                <td style={st.mono}>{p.quantity}</td>
                <td style={st.mono}>{fmt(p.cost_price)}</td>
                <td style={{ color: 'var(--text2)', fontSize: 12 }}>{fmtDate(p.opened_at)}</td>
                <td style={{ fontSize: 12 }}>{p.strategy_tag || '--'}</td>
                <td>
                  <button style={st.ghost} onClick={() => doClose(p)}>平仓</button>
                  <button style={{ ...st.ghost, color: 'var(--text3)' }} onClick={() => doDelete(p)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 历史(已平仓) */}
      <div className="panel">
        <div className="panel-header">
          历史持仓<span style={st.count}>{closed.length}</span>
          <label style={st.checkbox}>
            <input type="checkbox" checked={includeClosed} onChange={(e) => setIncludeClosed(e.target.checked)} />显示历史
          </label>
        </div>
        <table className="data-table">
          <thead><tr><th>标的</th><th>股数</th><th>开仓价</th><th>平仓价</th><th>盈亏</th><th></th></tr></thead>
          <tbody>
            {closed.length === 0 && <tr><td colSpan={6} style={st.empty}>暂无历史</td></tr>}
            {closed.map((p) => {
              const up = (p.profit_amount ?? 0) >= 0
              return (
                <tr key={p.id}>
                  <td><span style={{ fontWeight: 500, display: 'block' }}>{p.name || p.symbol}</span>
                    <span style={st.code}>{p.symbol}</span></td>
                  <td style={st.mono}>{p.quantity}</td>
                  <td style={st.mono}>{fmt(p.cost_price)}</td>
                  <td style={st.mono}>{fmt(p.close_price)}</td>
                  <td style={{ ...st.mono, color: p.profit_amount == null ? 'var(--text3)' : up ? 'var(--red)' : 'var(--green)' }}>
                    {p.profit_amount != null
                      ? <span>{up ? '+' : ''}{p.profit_amount.toFixed(0)}<span style={{ fontSize: 11, marginLeft: 4 }}>({up ? '+' : ''}{(p.profit_pct ?? 0).toFixed(2)}%)</span></span>
                      : '--'}
                  </td>
                  <td><button style={{ ...st.ghost, color: 'var(--text3)' }} onClick={() => doDelete(p)}>删除</button></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  page: { padding: 24, maxWidth: 1080, margin: '0 auto' },
  h1: { fontSize: 22, fontWeight: 700, margin: '0 0 4px' },
  sub: { color: 'var(--text3)', fontSize: 13, margin: '0 0 18px' },
  formGrid: { display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 },
  input: { width: '100%', minWidth: 0, background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 9px', fontSize: 13, outline: 'none' },
  btn: { background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6, padding: '8px 18px', cursor: 'pointer', fontSize: 13 },
  ghost: { background: 'transparent', color: 'var(--accent)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px', cursor: 'pointer', fontSize: 12, marginRight: 6 },
  err: { marginTop: 10, color: '#dc2626', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 6, padding: '8px 10px', fontSize: 13 },
  count: { fontSize: 11, color: 'var(--text3)', fontWeight: 400, marginLeft: 6 },
  checkbox: { display: 'flex', alignItems: 'center', gap: 5, color: 'var(--text2)', fontSize: 12, fontWeight: 400, marginLeft: 'auto' },
  empty: { textAlign: 'center', padding: 24, color: 'var(--text3)' },
  mono: { fontFamily: 'monospace' },
  code: { fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' },
}
