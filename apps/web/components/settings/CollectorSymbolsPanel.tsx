'use client'

import { useEffect, useState, type CSSProperties } from 'react'

import { SymbolSearch } from '@/components/SymbolSearch'
import {
  deleteCollectorSymbol,
  listCollectorSymbolStatus,
  upsertCollectorSymbol,
} from '@/lib/collector_symbols_api'
import { useMarket } from '@/lib/market-context'
import type { CollectorSymbolStatus, CollectorSymbolsStatusResponse } from '@/lib/types'

type CollectorForm = {
  symbol: string
  name: string
}

const emptyCollector: CollectorForm = { symbol: '', name: '' }

function formatTs(value: string | null): string {
  if (!value) return '--'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '--'
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function healthColor(health: CollectorSymbolStatus['health']): string {
  if (health === 'ok') return '#059669'
  if (health === 'warming') return '#b45309'
  if (health === 'stale') return '#dc2626'
  return 'var(--text3)'
}

function healthLabel(health: CollectorSymbolStatus['health']): string {
  if (health === 'ok') return '正常'
  if (health === 'warming') return '预热'
  if (health === 'stale') return '过期'
  return '停用'
}

export function CollectorSymbolsPanel() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const [collectorForm, setCollectorForm] = useState<CollectorForm>(emptyCollector)
  const [collectorData, setCollectorData] = useState<CollectorSymbolsStatusResponse | null>(null)
  const [includeDisabled, setIncludeDisabled] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const collectorRows = collectorData?.symbols ?? []

  const refreshCollectorSymbols = async () => {
    if (!isAshare) {
      setCollectorData(null)
      return
    }
    setCollectorData(await listCollectorSymbolStatus(market, includeDisabled))
  }

  useEffect(() => {
    void refreshCollectorSymbols()
      .catch((e) => setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, includeDisabled])

  const addCollectorSymbol = async () => {
    if (!isAshare || busy) return
    const symbol = collectorForm.symbol.trim().toUpperCase()
    if (!symbol) { setError('采集标的代码不能为空'); return }
    setBusy(true); setError(null); setNotice(null)
    try {
      const res = await upsertCollectorSymbol({
        market,
        symbol,
        name: collectorForm.name.trim() || null,
        enabled: true,
        collect_snapshot: true,
        collect_5m: true,
        collect_signals: true,
      })
      setCollectorForm(emptyCollector)
      await refreshCollectorSymbols()
      setNotice(
        res.collector_confirmed
          ? `${symbol} 已加入采集,collector 已确认${res.refill_queued ? ',5m 补数已排队' : ''}`
          : `${symbol} 已写入采集清单,但 collector 未确认:${res.collector_message ?? 'timeout'}`,
      )
    } catch (e) {
      setError(`添加采集标的失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy(false) }
  }

  const removeCollectorSymbol = async (row: CollectorSymbolStatus) => {
    const action = row.source === 'manual' ? '删除' : '停用'
    if (!window.confirm(`${action}采集标的 ${row.name || row.symbol}?`)) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const res = await deleteCollectorSymbol(market, row.symbol)
      await refreshCollectorSymbols()
      setNotice(
        res.collector_confirmed
          ? `${row.symbol} 已${action},collector 已确认`
          : `${row.symbol} 已${action},但 collector 未确认:${res.collector_message ?? 'timeout'}`,
      )
    } catch (e) {
      setError(`${action}采集标的失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy(false) }
  }

  if (!isAshare) {
    return (
      <div>
        <h1 style={st.h1}>采集标的</h1>
        <p style={st.sub}>{marketLabel.name}暂不支持采集清单维护,当前仅 A 股。</p>
      </div>
    )
  }

  return (
    <div>
      <div style={st.topLine}>
        <div>
          <h1 style={st.h1}>采集标的</h1>
          <p style={st.sub}>维护实盘采集进程会跟踪的标的清单,用于快照、5m 及以上 K 线入库和盘中消息生成。</p>
        </div>
        <div style={st.stats}>
          <span>启用 {collectorData?.enabled ?? 0}</span>
          <span>正常 {collectorData?.ok ?? 0}</span>
          <span>总计 {collectorData?.total ?? 0}</span>
        </div>
      </div>

      {error && <div style={st.err}>{error}</div>}
      {notice && <div style={st.ok}>{notice}</div>}

      <div style={st.grid}>
        <section className="panel" style={st.formPanel}>
          <div className="panel-header">添加标的</div>
          <div style={st.body}>
            <div style={{ position: 'relative', zIndex: 15, marginBottom: 10 }}>
              {collectorForm.symbol ? (
                <div style={st.picked}>
                  <span><b>{collectorForm.name || collectorForm.symbol}</b> <span style={st.code}>{collectorForm.symbol}</span></span>
                  <button style={st.ghost} onClick={() => setCollectorForm(emptyCollector)}>清空</button>
                </div>
              ) : (
                <SymbolSearch market={market} coreOnly={false}
                  placeholder="搜索代码或名称加入采集"
                  onSelect={(hit: any) => setCollectorForm({ symbol: hit.symbol, name: hit.name || '' })} />
              )}
            </div>
            <div style={st.collectorForm}>
              <input style={st.input} placeholder="股票代码" value={collectorForm.symbol} onChange={(e) => setCollectorForm({ ...collectorForm, symbol: e.target.value.toUpperCase() })} />
              <button style={st.btn} disabled={busy} onClick={() => void addCollectorSymbol()}>{busy ? '处理中' : '加入采集'}</button>
            </div>
            <div style={st.hintGrid}>
              <span>快照</span>
              <span>5m+ K 线</span>
              <span>信号扫描</span>
              <span>collector 确认</span>
            </div>
          </div>
        </section>

        <section className="panel" style={st.listPanel}>
          <div className="panel-header">
            采集清单
            <label style={st.check}>
              <input type="checkbox" checked={includeDisabled} onChange={(e) => setIncludeDisabled(e.target.checked)} />
              显示停用
            </label>
          </div>
          <div style={st.body}>
            <div style={st.collectorStats}>
              <span>启用 {collectorData?.enabled ?? 0}</span>
              <span>正常 {collectorData?.ok ?? 0}</span>
              <span>预热 {collectorData?.warming ?? 0}</span>
              <span>过期 {collectorData?.stale ?? 0}</span>
              <span>快照 {collectorData?.snapshot_count ?? 0}</span>
              <span>5m+ {collectorData?.kline_5m_count ?? 0}</span>
              <span>信号 {collectorData?.signals_count ?? 0}</span>
              <span>总计 {collectorData?.total ?? 0}</span>
            </div>
            <div style={st.collectorList}>
              {collectorRows.length === 0 && <div style={st.empty}>暂无采集标的</div>}
              {collectorRows.map((row) => (
                <div key={row.symbol} style={{ ...st.collectorRow, opacity: row.enabled ? 1 : 0.5 }}>
                  <div>
                    <div style={st.collectorName}>{row.name || row.symbol}</div>
                    <div style={st.code}>{row.symbol}</div>
                  </div>
                  <div style={st.collectorFlags}>
                    <span style={{ color: healthColor(row.health), fontWeight: 700 }}>{healthLabel(row.health)}</span>
                    <span>{row.health_reason}</span>
                    <span>快照 {formatTs(row.snapshot_ts)}</span>
                    <span>5m {formatTs(row.kline_5m_ts)}</span>
                    <span>{row.source === 'core' ? '核心' : row.source === 'seed' ? '内置' : '手动'}</span>
                    {row.collect_snapshot && <span>快照</span>}
                    {row.collect_5m && <span>5m+</span>}
                    {row.collect_signals && <span>信号</span>}
                  </div>
                  <button style={st.ghostDanger} disabled={busy} onClick={() => void removeCollectorSymbol(row)}>
                    {row.source === 'manual' ? '删除' : '停用'}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  topLine: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 },
  h1: { fontSize: 22, fontWeight: 700, margin: '0 0 4px' },
  sub: { color: 'var(--text3)', fontSize: 13, margin: '0 0 18px' },
  stats: { display: 'flex', gap: 8, color: 'var(--text2)', fontSize: 12, whiteSpace: 'nowrap' },
  grid: { display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: 16, alignItems: 'start' },
  formPanel: { minHeight: 240 },
  listPanel: { minHeight: 560 },
  body: { padding: '14px 18px' },
  collectorForm: { display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'center', marginBottom: 10 },
  input: { width: '100%', minWidth: 0, background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 9px', fontSize: 13, outline: 'none' },
  btn: { background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6, padding: '8px 14px', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap' },
  ghost: { background: 'transparent', color: 'var(--accent)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', cursor: 'pointer', fontSize: 12, marginRight: 6 },
  ghostDanger: { background: 'transparent', color: '#dc2626', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', cursor: 'pointer', fontSize: 12, marginRight: 6 },
  picked: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '9px 12px', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 },
  code: { fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' },
  empty: { textAlign: 'center', padding: 24, color: 'var(--text3)' },
  err: { marginBottom: 12, color: '#dc2626', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 6, padding: '8px 10px', fontSize: 13 },
  ok: { marginBottom: 12, color: '#047857', background: 'rgba(16,185,129,0.10)', border: '1px solid rgba(16,185,129,0.25)', borderRadius: 6, padding: '8px 10px', fontSize: 13 },
  check: { marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text2)', fontSize: 12, fontWeight: 400 },
  hintGrid: { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6, color: 'var(--text3)', fontSize: 11 },
  collectorStats: { display: 'grid', gridTemplateColumns: 'repeat(8, minmax(0, 1fr))', gap: 6, marginBottom: 12, color: 'var(--text2)', fontSize: 11 },
  collectorList: { display: 'grid', gap: 6, maxHeight: 680, overflow: 'auto' },
  collectorRow: { display: 'grid', gridTemplateColumns: 'minmax(0, 0.9fr) minmax(0, 1.7fr) auto', alignItems: 'center', gap: 10, border: '1px solid var(--border)', borderRadius: 6, background: 'var(--bg2)', padding: '8px 9px' },
  collectorName: { fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  collectorFlags: { display: 'flex', flexWrap: 'wrap', gap: 5, color: 'var(--text3)', fontSize: 10 },
}
