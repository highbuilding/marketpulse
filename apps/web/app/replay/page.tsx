'use client'

import { type CSSProperties, useEffect, useState } from 'react'
import useSWR from 'swr'

import { ReplayChart, type ReplayChartPoint } from '@/components/ReplayChart'
import { useMarket } from '@/lib/market-context'
import { fetchReplay } from '@/lib/replay_api'
import { fetchBarsHistory } from '@/lib/symbol_api'
import type { LiveMessageCategory, ReplayMessage, ThemeSeries } from '@/lib/types'

const INDICES = [
  { symbol: '000001.SH', name: '上证指数' },
  { symbol: '399001.SZ', name: '深证成指' },
  { symbol: '399006.SZ', name: '创业板指' },
  { symbol: '000300.SH', name: '沪深300' },
  { symbol: '000905.SH', name: '中证500' },
  { symbol: '000852.SH', name: '中证1000' },
  { symbol: '000688.SH', name: '科创50' },
  { symbol: '000016.SH', name: '上证50' },
]

const CATEGORIES = [
  { id: 'all', label: '全部' },
  { id: 'index', label: '指数' },
  { id: 'theme', label: '题材' },
  { id: 'watchlist', label: '自选' },
  { id: 'signal', label: 'CD 信号' },
  { id: 'risk', label: '风险' },
]

const MESSAGE_PAGE_SIZE = 200

const LEVEL_COLOR: Record<string, string> = {
  info: 'var(--text3)', watch: '#e0a73e', warning: '#f59e0b', critical: '#ef4444',
}
const CATEGORY_LABEL: Record<string, string> = {
  index: '指数', theme: '题材', watchlist: '自选', signal: 'CD', risk: '风险', system: '系统',
}

function todayBjt(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit',
  })
}

// 日线级周期 (1d/1wk/1mo) bar_ts 是自然日 00:00, 只显日期。
const _DAY_INTERVALS = new Set(['1d', '1wk', '1mo'])
function fmtMsgTime(m: ReplayMessage): string {
  const iv = (m.payload as Record<string, unknown>)?.interval
  if (typeof iv === 'string' && _DAY_INTERVALS.has(iv)) {
    return new Date(m.ts).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
  }
  return fmtTime(m.ts)
}

function pct(v: number | null | undefined): string {
  if (v == null) return '--'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

// 当天 BJT 自然日的 UTC 窗口边界
function dayBounds(date: string): { startMs: number; endMs: number } {
  const startMs = Date.parse(`${date}T00:00:00+08:00`)
  return { startMs, endMs: startMs + 24 * 3600 * 1000 }
}

// 拉某指数当天 5m 收盘价序列 (复用 bars/history 转发链路, filter 到当天窗口)
async function fetchIndexDayBars(
  symbol: string, date: string,
): Promise<{ points: ReplayChartPoint[]; baseline: number | null }> {
  const { startMs, endMs } = dayBounds(date)
  const before = new Date(endMs).toISOString()
  const resp = await fetchBarsHistory(symbol, '5m', { before, limit: 60 })
  const inDay = (resp.bars ?? []).filter((b) => {
    const t = Date.parse(b.ts)
    return t >= startMs && t < endMs
  })
  const points = inDay.map((b) => ({ ts: b.ts, value: b.close }))
  const baseline = inDay.length > 0 ? inDay[0].open : null
  return { points, baseline }
}

export default function ReplayPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const [date, setDate] = useState(todayBjt())
  const [indexSymbol, setIndexSymbol] = useState(INDICES[0].symbol)
  const [catFilter, setCatFilter] = useState('all')
  const [messagePage, setMessagePage] = useState(0)
  const messageOffset = messagePage * MESSAGE_PAGE_SIZE
  const category = catFilter === 'all' ? undefined : (catFilter as LiveMessageCategory)

  useEffect(() => {
    setMessagePage(0)
  }, [date, catFilter])

  const { data, isLoading } = useSWR(
    isAshare ? ['replay', market, date, catFilter, messageOffset] : null,
    () => fetchReplay(market, date, {
      category,
      msgLimit: MESSAGE_PAGE_SIZE,
      msgOffset: messageOffset,
    }),
    { revalidateOnFocus: false },
  )

  const { data: indexData } = useSWR(
    isAshare ? ['replay-index', indexSymbol, date] : null,
    () => fetchIndexDayBars(indexSymbol, date),
    { revalidateOnFocus: false },
  )

  const messages = data?.messages ?? []
  const themeSeries = data?.theme_series ?? []
  const messageTotal = data?.message_total ?? 0
  const totalPages = Math.max(1, Math.ceil(messageTotal / MESSAGE_PAGE_SIZE))

  if (!isAshare) {
    return (
      <main style={styles.page}>
        <section style={styles.header}>
          <div>
            <div style={styles.eyebrow}>盘后回放</div>
            <h1 style={styles.title}>{marketLabel.name}暂未接入盘后回放</h1>
            <p style={styles.subtle}>盘后回放第一版只支持 A 股。</p>
          </div>
        </section>
      </main>
    )
  }

  return (
    <main style={styles.page}>
      <section style={styles.header}>
        <div>
          <div style={styles.eyebrow}>A 股盘后回放</div>
          <h1 style={styles.title}>盘中时间轴复盘</h1>
          <p style={styles.subtle}>
            按交易日复盘实盘消息、题材状态变迁与核心指数走势。事实源:live_messages + theme_snapshots。
          </p>
        </div>
        <div style={styles.dateBox}>
          <label style={styles.dateLabel}>交易日</label>
          <input type="date" value={date} max={todayBjt()}
            onChange={(e) => setDate(e.target.value)} style={styles.dateInput} />
        </div>
      </section>

      {data?.degraded && data.degraded.length > 0 && (
        <div style={styles.degraded}>部分数据不可用:{data.degraded.join('、')}</div>
      )}

      {/* 泳道 1: 指数背景 */}
      <section style={styles.panel}>
        <div style={styles.panelHead}>
          <h2 style={styles.panelTitle}>指数背景</h2>
          <select value={indexSymbol} onChange={(e) => setIndexSymbol(e.target.value)} style={styles.select}>
            {INDICES.map((idx) => <option key={idx.symbol} value={idx.symbol}>{idx.name}</option>)}
          </select>
        </div>
        {indexData && indexData.points.length > 0 ? (
          <ReplayChart points={indexData.points} baseline={indexData.baseline} height={300} />
        ) : (
          <div style={styles.emptyState}>该交易日暂无指数 5m 数据</div>
        )}
        <div style={styles.footnote}>基准线为当日首根 5m 开盘价;数据走 K 线历史链路(collector 转发)。</div>
      </section>

      {/* 泳道 2: 题材状态变迁 */}
      <section style={styles.panel}>
        <h2 style={styles.panelTitle}>题材状态变迁</h2>
        <p style={styles.panelDesc}>每个题材当日上涨占比(up_ratio)随时间走势,按活跃度排序。</p>
        {themeSeries.length === 0 ? (
          <div style={styles.emptyState}>该交易日暂无题材快照</div>
        ) : (
          <div style={styles.themeGrid}>
            {themeSeries.map((t) => <ThemeRow key={t.theme_code} series={t} />)}
          </div>
        )}
      </section>

      {/* 泳道 3: 消息时间轴 */}
      <section style={styles.panel}>
        <div style={styles.panelHead}>
          <div>
            <h2 style={styles.panelTitle}>实盘消息时间轴</h2>
            <p style={styles.panelDesc}>
              {messageTotal > 0
                ? `共 ${messageTotal} 条, 当前第 ${messagePage + 1}/${totalPages} 页`
                : '按时间升序展示当日实盘消息'}
            </p>
          </div>
          <div style={styles.filterRow}>
            {CATEGORIES.map((c) => (
              <button key={c.id} onClick={() => setCatFilter(c.id)}
                style={{ ...styles.filterBtn, ...(catFilter === c.id ? styles.filterBtnActive : {}) }}>
                {c.label}
              </button>
            ))}
          </div>
        </div>
        {isLoading && messages.length === 0 && <div style={styles.emptyState}>加载中</div>}
        {!isLoading && messages.length === 0 && (
          <div style={styles.emptyState}>该交易日{catFilter === 'all' ? '' : '该分类'}暂无消息</div>
        )}
        <div style={styles.timeline}>
          {messages.map((m) => <MessageRow key={m.id} m={m} />)}
        </div>
        {messageTotal > MESSAGE_PAGE_SIZE && (
          <div style={styles.pager}>
            <button
              type="button"
              onClick={() => setMessagePage((p) => Math.max(0, p - 1))}
              disabled={messagePage === 0 || isLoading}
              style={{
                ...styles.pageBtn,
                ...((messagePage === 0 || isLoading) ? styles.pageBtnDisabled : {}),
              }}
            >
              上一页
            </button>
            <span style={styles.pageMeta}>
              {messageOffset + 1}-{Math.min(messageOffset + messages.length, messageTotal)} / {messageTotal}
            </span>
            <button
              type="button"
              onClick={() => setMessagePage((p) => p + 1)}
              disabled={!data?.message_has_more || isLoading}
              style={{
                ...styles.pageBtn,
                ...((!data?.message_has_more || isLoading) ? styles.pageBtnDisabled : {}),
              }}
            >
              下一页
            </button>
          </div>
        )}
      </section>
    </main>
  )
}

function ThemeRow({ series }: { series: ThemeSeries }) {
  const ups = series.points.map((p) => p.up_ratio).filter((v): v is number => v != null)
  const last = series.points[series.points.length - 1]
  return (
    <div style={styles.themeRow}>
      <div style={styles.themeInfo}>
        <div style={styles.themeName}>{series.theme_name}</div>
        <div style={styles.themeMeta}>
          <span>涨占比 {last?.up_ratio != null ? `${(last.up_ratio * 100).toFixed(0)}%` : '--'}</span>
          <span style={{ color: (last?.pct_change ?? 0) >= 0 ? '#ef4444' : '#22c55e' }}>
            {pct(last?.pct_change)}
          </span>
          <span style={styles.themeDim}>{series.points.length} 点</span>
        </div>
      </div>
      <Sparkline values={ups} />
    </div>
  )
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div style={styles.sparkEmpty}>数据不足</div>
  const w = 160, h = 36
  const min = Math.min(...values), max = Math.max(...values)
  const span = max - min || 1
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w
    const y = h - ((v - min) / span) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const rising = values[values.length - 1] >= values[0]
  return (
    <svg width={w} height={h} style={{ flexShrink: 0 }}>
      <polyline points={pts} fill="none"
        stroke={rising ? '#ef4444' : '#22c55e'} strokeWidth={1.5} />
    </svg>
  )
}

function MessageRow({ m }: { m: ReplayMessage }) {
  return (
    <article style={styles.msg}>
      <div style={styles.msgTime}>{fmtMsgTime(m)}</div>
      <div style={{ ...styles.msgBar, background: LEVEL_COLOR[m.level] ?? 'var(--text3)' }} />
      <div style={styles.msgBody}>
        <div style={styles.msgTop}>
          <span style={styles.msgTag}>{CATEGORY_LABEL[m.category] ?? m.category}</span>
          <b style={styles.msgTitle}>{m.title}</b>
        </div>
        <p style={styles.msgText}>{m.body}</p>
      </div>
    </article>
  )
}

const styles: Record<string, CSSProperties> = {
  page: { padding: 24, maxWidth: 1280, margin: '0 auto' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, marginBottom: 20 },
  eyebrow: { color: 'var(--accent)', fontSize: 12, fontWeight: 700, marginBottom: 6 },
  title: { margin: 0, fontSize: 24, fontWeight: 700 },
  subtle: { color: 'var(--text2)', fontSize: 14, margin: '8px 0 0', lineHeight: 1.6, maxWidth: 720 },
  dateBox: { display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-end' },
  dateLabel: { color: 'var(--text3)', fontSize: 12 },
  dateInput: { background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 9px', fontSize: 13 },
  degraded: { marginBottom: 12, border: '1px solid #f59e0b', borderRadius: 8, padding: '8px 12px', color: '#f59e0b', fontSize: 13, background: 'rgba(245,158,11,0.08)' },
  panel: { border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg2)', padding: 16, marginBottom: 16 },
  panelHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, gap: 12, flexWrap: 'wrap' },
  panelTitle: { margin: 0, fontSize: 16, fontWeight: 700 },
  panelDesc: { margin: '6px 0 10px', color: 'var(--text3)', fontSize: 13, lineHeight: 1.5 },
  select: { background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 9px', fontSize: 13 },
  footnote: { marginTop: 8, color: 'var(--text3)', fontSize: 11 },
  emptyState: { marginTop: 4, border: '1px dashed var(--border)', borderRadius: 8, padding: 20, color: 'var(--text3)', fontSize: 13, textAlign: 'center' },
  themeGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 10, marginTop: 4 },
  themeRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--bg)' },
  themeInfo: { minWidth: 0 },
  themeName: { fontSize: 13, fontWeight: 700, marginBottom: 4 },
  themeMeta: { display: 'flex', gap: 10, fontSize: 12, color: 'var(--text2)', flexWrap: 'wrap' },
  themeDim: { color: 'var(--text3)' },
  sparkEmpty: { fontSize: 11, color: 'var(--text3)', width: 160, textAlign: 'right' },
  filterRow: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  filterBtn: { background: 'transparent', color: 'var(--text2)', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 12 },
  filterBtnActive: { background: 'var(--accent)', color: '#fff', borderColor: 'var(--accent)' },
  timeline: { display: 'grid', gap: 8, marginTop: 4 },
  pager: { display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 10, marginTop: 12, flexWrap: 'wrap' },
  pageBtn: { background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px', cursor: 'pointer', fontSize: 12 },
  pageBtnDisabled: { opacity: 0.45, cursor: 'not-allowed' },
  pageMeta: { color: 'var(--text3)', fontSize: 12 },
  msg: { display: 'flex', gap: 10, alignItems: 'stretch', border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--bg)' },
  msgTime: { fontFamily: 'monospace', fontSize: 12, color: 'var(--text3)', width: 44, flexShrink: 0, paddingTop: 1 },
  msgBar: { width: 3, borderRadius: 2, flexShrink: 0 },
  msgBody: { minWidth: 0 },
  msgTop: { display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 2 },
  msgTag: { fontSize: 11, color: 'var(--text3)', border: '1px solid var(--border)', borderRadius: 4, padding: '0 5px', flexShrink: 0 },
  msgTitle: { fontSize: 13 },
  msgText: { margin: 0, color: 'var(--text2)', fontSize: 12, lineHeight: 1.5 },
}
