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
  symbol: '',
  name: '',
  quantity: '',
  cost_price: '',
  strategy_tag: '',
  entry_reason: '',
  note: '',
}

function parseNumber(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const n = Number(trimmed)
  return Number.isFinite(n) ? n : null
}

function fmtMoney(value: number | null): string {
  if (value == null) return '--'
  return value.toFixed(2)
}

function fmtDate(value: string | null): string {
  if (!value) return '--'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '--'
  return d.toLocaleString('zh-CN', { hour12: false })
}

export default function AssistantPage() {
  const { market, marketLabel } = useMarket()
  const [positions, setPositions] = useState<Position[]>([])
  const [form, setForm] = useState<FormState>(emptyForm)
  const [includeClosed, setIncludeClosed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isAshare = market === 'ashare'
  const activePositions = useMemo(
    () => positions.filter((p) => p.status !== 'closed'),
    [positions],
  )

  const refresh = async () => {
    if (!isAshare) {
      setPositions([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await listPositions(market, includeClosed)
      setPositions(data.positions)
    } catch (e) {
      setError(`持仓加载失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, includeClosed])

  const submit = async () => {
    if (!isAshare || saving) return
    const symbol = form.symbol.trim().toUpperCase()
    if (!symbol) {
      setError('请填写股票代码')
      return
    }
    const quantity = parseNumber(form.quantity)
    const cost = parseNumber(form.cost_price)
    setSaving(true)
    setError(null)
    try {
      await upsertPosition({
        market,
        symbol,
        name: form.name.trim() || null,
        quantity: quantity == null ? 0 : Math.trunc(quantity),
        cost_price: cost,
        strategy_tag: form.strategy_tag.trim() || null,
        entry_reason: form.entry_reason.trim() || null,
        note: form.note.trim() || null,
        status: 'active',
      })
      setForm(emptyForm)
      await refresh()
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
      await closePosition(market, symbol)
      await refresh()
    } catch (e) {
      setError(`清仓失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  if (!isAshare) {
    return (
      <main style={styles.page}>
        <section style={styles.header}>
          <div>
            <div style={styles.eyebrow}>AI 助手</div>
            <h1 style={styles.title}>{marketLabel.name}暂未接入 AI 题材决策</h1>
            <p style={styles.subtle}>
              当前 AI 盘中决策助手第一版只支持 A 股。其他市场后续会按各自交易结构单独设计。
            </p>
          </div>
        </section>
      </main>
    )
  }

  return (
    <main style={styles.page}>
      <section style={styles.header}>
        <div>
          <div style={styles.eyebrow}>A 股 AI 助手</div>
          <h1 style={styles.title}>盘中结论与持仓观察</h1>
          <p style={styles.subtle}>
            第一版先接入手动持仓。题材雷达、候选机会和 AI 结论会在后续阶段接入。
          </p>
        </div>
        <div style={styles.summary}>
          <span style={styles.summaryLabel}>活跃持仓</span>
          <strong style={styles.summaryValue}>{activePositions.length}</strong>
        </div>
      </section>

      <section style={styles.grid}>
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <div>
              <h2 style={styles.panelTitle}>手动持仓</h2>
              <p style={styles.panelDesc}>用于后续 AI 持仓建议，不接券商、不自动交易。</p>
            </div>
            <label style={styles.checkboxLabel}>
              <input
                type="checkbox"
                checked={includeClosed}
                onChange={(e) => setIncludeClosed(e.target.checked)}
              />
              显示已清仓
            </label>
          </div>

          <div style={styles.formGrid}>
            <input style={styles.input} placeholder="代码 002415.SZ" value={form.symbol}
              onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
            <input style={styles.input} placeholder="名称" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input style={styles.input} placeholder="数量" value={form.quantity}
              onChange={(e) => setForm({ ...form, quantity: e.target.value })} />
            <input style={styles.input} placeholder="成本价" value={form.cost_price}
              onChange={(e) => setForm({ ...form, cost_price: e.target.value })} />
            <input style={styles.input} placeholder="策略标签" value={form.strategy_tag}
              onChange={(e) => setForm({ ...form, strategy_tag: e.target.value })} />
            <input style={styles.input} placeholder="买入理由" value={form.entry_reason}
              onChange={(e) => setForm({ ...form, entry_reason: e.target.value })} />
            <input style={{ ...styles.input, gridColumn: '1 / -1' }} placeholder="备注" value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </div>
          <div style={styles.actions}>
            <button style={styles.primaryButton} onClick={submit} disabled={saving}>
              {saving ? '保存中' : '保存持仓'}
            </button>
          </div>

          {error && <div style={styles.error}>{error}</div>}

          <div style={styles.tableWrap}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>标的</th>
                  <th style={styles.th}>数量</th>
                  <th style={styles.th}>成本</th>
                  <th style={styles.th}>策略</th>
                  <th style={styles.th}>状态</th>
                  <th style={styles.th}>更新</th>
                  <th style={styles.th}></th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr><td style={styles.td} colSpan={7}>加载中...</td></tr>
                )}
                {!loading && positions.length === 0 && (
                  <tr><td style={styles.td} colSpan={7}>暂无持仓。先录入关注或持仓标的。</td></tr>
                )}
                {!loading && positions.map((p) => (
                  <tr key={`${p.market}:${p.symbol}`}>
                    <td style={styles.td}>
                      <div style={styles.symbol}>{p.symbol}</div>
                      <div style={styles.muted}>{p.name || '--'}</div>
                    </td>
                    <td style={styles.td}>{p.quantity}</td>
                    <td style={styles.td}>{fmtMoney(p.cost_price)}</td>
                    <td style={styles.td}>
                      <div>{p.strategy_tag || '--'}</div>
                      <div style={styles.muted}>{p.entry_reason || p.note || '--'}</div>
                    </td>
                    <td style={styles.td}>
                      <span style={p.status === 'closed' ? styles.closedBadge : styles.activeBadge}>
                        {p.status === 'closed' ? '已清仓' : '持有'}
                      </span>
                    </td>
                    <td style={styles.td}>{fmtDate(p.updated_at)}</td>
                    <td style={styles.td}>
                      {p.status !== 'closed' && (
                        <button style={styles.ghostButton} onClick={() => void close(p.symbol)}>清仓</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <aside style={styles.side}>
          <div style={styles.panel}>
            <h2 style={styles.panelTitle}>AI 结论</h2>
            <p style={styles.panelDesc}>
              等 Phase 6 接入后，这里展示盘面状态、开单观察、风险和持仓建议。
            </p>
            <div style={styles.emptyState}>等待 AITradeOpinionService 接入</div>
          </div>
          <div style={styles.panel}>
            <h2 style={styles.panelTitle}>通知候选</h2>
            <p style={styles.panelDesc}>
              系统会先生成可通知结论和冷却状态，外部渠道暂不发送。
            </p>
            <div style={styles.emptyState}>等待通知能力骨架接入</div>
          </div>
        </aside>
      </section>
    </main>
  )
}

const styles: Record<string, CSSProperties> = {
  page: {
    padding: 24,
    maxWidth: 1280,
    margin: '0 auto',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 16,
    marginBottom: 20,
  },
  eyebrow: {
    color: 'var(--accent)',
    fontSize: 12,
    fontWeight: 700,
    marginBottom: 6,
  },
  title: {
    margin: 0,
    fontSize: 24,
    fontWeight: 700,
    letterSpacing: 0,
  },
  subtle: {
    color: 'var(--text2)',
    fontSize: 14,
    margin: '8px 0 0',
    lineHeight: 1.6,
  },
  summary: {
    border: '1px solid var(--border)',
    borderRadius: 8,
    padding: '10px 14px',
    minWidth: 120,
    background: 'var(--bg2)',
  },
  summaryLabel: {
    display: 'block',
    color: 'var(--text3)',
    fontSize: 12,
    marginBottom: 4,
  },
  summaryValue: {
    color: 'var(--text)',
    fontSize: 24,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) 320px',
    gap: 16,
  },
  panel: {
    border: '1px solid var(--border)',
    borderRadius: 8,
    background: 'var(--bg2)',
    padding: 16,
  },
  panelHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 14,
  },
  panelTitle: {
    margin: 0,
    fontSize: 16,
    fontWeight: 700,
  },
  panelDesc: {
    margin: '6px 0 0',
    color: 'var(--text3)',
    fontSize: 13,
    lineHeight: 1.5,
  },
  checkboxLabel: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    color: 'var(--text2)',
    fontSize: 13,
    whiteSpace: 'nowrap',
  },
  formGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
    gap: 10,
  },
  input: {
    width: '100%',
    minWidth: 0,
    background: 'var(--bg3)',
    color: 'var(--text)',
    border: '1px solid var(--border)',
    borderRadius: 6,
    padding: '9px 10px',
    fontSize: 13,
    outline: 'none',
  },
  actions: {
    display: 'flex',
    justifyContent: 'flex-end',
    marginTop: 12,
  },
  primaryButton: {
    background: 'var(--accent)',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    padding: '9px 16px',
    cursor: 'pointer',
    fontSize: 13,
  },
  ghostButton: {
    background: 'transparent',
    color: 'var(--accent)',
    border: '1px solid var(--border)',
    borderRadius: 6,
    padding: '6px 10px',
    cursor: 'pointer',
    fontSize: 12,
    whiteSpace: 'nowrap',
  },
  error: {
    marginTop: 12,
    color: '#dc2626',
    background: 'rgba(220, 38, 38, 0.08)',
    border: '1px solid rgba(220, 38, 38, 0.2)',
    borderRadius: 6,
    padding: '8px 10px',
    fontSize: 13,
  },
  tableWrap: {
    overflowX: 'auto',
    marginTop: 14,
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  },
  th: {
    textAlign: 'left',
    color: 'var(--text3)',
    fontWeight: 600,
    borderBottom: '1px solid var(--border)',
    padding: '9px 8px',
    whiteSpace: 'nowrap',
  },
  td: {
    borderBottom: '1px solid var(--border)',
    padding: '10px 8px',
    verticalAlign: 'top',
    color: 'var(--text)',
  },
  symbol: {
    fontWeight: 700,
  },
  muted: {
    color: 'var(--text3)',
    fontSize: 12,
    marginTop: 2,
    maxWidth: 220,
  },
  activeBadge: {
    display: 'inline-block',
    borderRadius: 999,
    padding: '3px 8px',
    color: '#15803d',
    background: 'rgba(21, 128, 61, 0.1)',
  },
  closedBadge: {
    display: 'inline-block',
    borderRadius: 999,
    padding: '3px 8px',
    color: 'var(--text3)',
    background: 'var(--bg3)',
  },
  side: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  emptyState: {
    marginTop: 12,
    border: '1px dashed var(--border)',
    borderRadius: 8,
    padding: 16,
    color: 'var(--text3)',
    fontSize: 13,
    textAlign: 'center',
  },
}
