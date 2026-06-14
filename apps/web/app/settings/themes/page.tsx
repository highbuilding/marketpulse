'use client'

import { useEffect, useMemo, useState, type CSSProperties } from 'react'

import { SymbolSearch } from '@/components/SymbolSearch'
import { useMarket } from '@/lib/market-context'
import type { ThemeClassification, ThemeConstituent, ThemeDefinition, ThemePriority } from '@/lib/types'
import {
  createTheme,
  deleteConstituent,
  deleteTheme,
  getTheme,
  listThemes,
  patchTheme,
  upsertConstituent,
} from '@/lib/themes_api'

const classifications: Array<{ value: ThemeClassification; label: string }> = [
  { value: 'index_weight', label: '大盘影响' },
  { value: 'theme', label: '当前题材' },
  { value: 'industry', label: '行业板块' },
  { value: 'concept', label: '概念板块' },
  { value: 'watch', label: '自选关联' },
]
const priorities: ThemePriority[] = ['P0', 'P1', 'P2', 'P3']
const roleHints = ['leader', 'core', 'mid_core', 'follower', 'laggard', 'watch']

type ThemeForm = {
  theme_code: string
  theme_name: string
  classification: ThemeClassification
  priority: ThemePriority
  note: string
}

type MemberForm = {
  symbol: string
  name: string
  role_hint: string
  weight: string
  note: string
}

const emptyTheme: ThemeForm = {
  theme_code: '',
  theme_name: '',
  classification: 'theme',
  priority: 'P2',
  note: '',
}
const emptyMember: MemberForm = {
  symbol: '',
  name: '',
  role_hint: 'core',
  weight: '',
  note: '',
}

function numOrNull(value: string): number | null {
  const text = value.trim()
  if (!text) return null
  const num = Number(text)
  return Number.isFinite(num) ? num : null
}

function classLabel(value: string): string {
  return classifications.find((c) => c.value === value)?.label ?? value
}

export default function ThemeSettingsPage() {
  const { market, marketLabel } = useMarket()
  const isAshare = market === 'ashare'
  const [themes, setThemes] = useState<ThemeDefinition[]>([])
  const [selectedCode, setSelectedCode] = useState<string | null>(null)
  const [selected, setSelected] = useState<ThemeDefinition | null>(null)
  const [members, setMembers] = useState<ThemeConstituent[]>([])
  const [themeForm, setThemeForm] = useState<ThemeForm>(emptyTheme)
  const [memberForm, setMemberForm] = useState<MemberForm>(emptyMember)
  const [includeDisabled, setIncludeDisabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const activeCount = useMemo(() => themes.filter((t) => t.enabled).length, [themes])

  const refreshDetail = async (code: string) => {
    const data = await getTheme(market, code)
    setSelected(data.theme)
    setMembers(data.constituents)
    setThemeForm({
      theme_code: data.theme.theme_code,
      theme_name: data.theme.theme_name,
      classification: data.theme.classification,
      priority: data.theme.priority,
      note: data.theme.note ?? '',
    })
  }

  const refreshThemes = async (nextSelected?: string | null) => {
    if (!isAshare) {
      setThemes([])
      setSelected(null)
      setMembers([])
      return
    }
    const data = await listThemes(market, includeDisabled)
    setThemes(data.themes)
    const code = nextSelected ?? selectedCode ?? data.themes[0]?.theme_code ?? null
    setSelectedCode(code)
    if (code) await refreshDetail(code)
  }

  useEffect(() => {
    void refreshThemes().catch((e) => setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, includeDisabled])

  const selectTheme = async (code: string) => {
    setSelectedCode(code)
    setError(null)
    try { await refreshDetail(code) }
    catch (e) { setError(`加载题材失败: ${e instanceof Error ? e.message : String(e)}`) }
  }

  const saveTheme = async () => {
    if (!isAshare || saving) return
    const name = themeForm.theme_name.trim()
    if (!name) { setError('题材名称不能为空'); return }
    setSaving(true); setError(null)
    try {
      if (selected) {
        await patchTheme(market, selected.theme_code, {
          theme_name: name,
          classification: themeForm.classification,
          priority: themeForm.priority,
          note: themeForm.note.trim() || null,
        })
        await refreshThemes(selected.theme_code)
      } else {
        const res = await createTheme({
          market,
          theme_code: themeForm.theme_code.trim() || null,
          theme_name: name,
          classification: themeForm.classification,
          priority: themeForm.priority,
          enabled: true,
          note: themeForm.note.trim() || null,
        })
        setThemeForm(emptyTheme)
        await refreshThemes(res.theme_code)
      }
    } catch (e) {
      setError(`保存失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally { setSaving(false) }
  }

  const newTheme = () => {
    setSelectedCode(null)
    setSelected(null)
    setMembers([])
    setThemeForm(emptyTheme)
    setMemberForm(emptyMember)
    setError(null)
  }

  const toggleTheme = async (theme: ThemeDefinition) => {
    try {
      await patchTheme(market, theme.theme_code, { enabled: !theme.enabled })
      await refreshThemes(theme.theme_code)
    } catch (e) {
      setError(`切换失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const removeTheme = async (theme: ThemeDefinition) => {
    const action = theme.source === 'seed' ? '停用' : '删除'
    if (!window.confirm(`${action} ${theme.theme_name}?`)) return
    try {
      await deleteTheme(market, theme.theme_code)
      await refreshThemes(null)
    } catch (e) {
      setError(`${action}失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const saveMember = async () => {
    if (!selected) return
    const symbol = memberForm.symbol.trim().toUpperCase()
    if (!symbol) { setError('成分股代码不能为空'); return }
    try {
      await upsertConstituent(market, selected.theme_code, {
        symbol,
        name: memberForm.name.trim() || null,
        role_hint: memberForm.role_hint.trim() || null,
        weight: numOrNull(memberForm.weight),
        enabled: true,
        note: memberForm.note.trim() || null,
      })
      setMemberForm(emptyMember)
      await refreshDetail(selected.theme_code)
      await refreshThemes(selected.theme_code)
    } catch (e) {
      setError(`保存成分股失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const editMember = (member: ThemeConstituent) => {
    setMemberForm({
      symbol: member.symbol,
      name: member.name ?? '',
      role_hint: member.role_hint ?? 'core',
      weight: member.weight == null ? '' : String(member.weight),
      note: member.note ?? '',
    })
  }

  const removeMember = async (member: ThemeConstituent) => {
    if (!selected) return
    const action = member.source === 'seed' ? '停用' : '删除'
    if (!window.confirm(`${action} ${member.name || member.symbol}?`)) return
    try {
      await deleteConstituent(market, selected.theme_code, member.symbol)
      await refreshDetail(selected.theme_code)
      await refreshThemes(selected.theme_code)
    } catch (e) {
      setError(`${action}成分股失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  if (!isAshare) {
    return (
      <div>
        <h1 style={st.h1}>题材库</h1>
        <p style={st.sub}>{marketLabel.name}暂不支持题材库维护,当前仅 A 股。</p>
      </div>
    )
  }

  return (
    <div>
      <div style={st.topLine}>
        <div>
          <h1 style={st.h1}>题材库</h1>
          <p style={st.sub}>维护盘中雷达实际跟踪的板块、主线题材和成分股。</p>
        </div>
        <div style={st.stats}>
          <span>启用 {activeCount}</span>
          <span>总计 {themes.length}</span>
        </div>
      </div>
      {activeCount > 45 && <div style={st.warn}>启用题材已超过 45 个,盘中跟踪会变得分散。</div>}
      {error && <div style={st.err}>{error}</div>}

      <div style={st.grid}>
        <section className="panel" style={st.side}>
          <div className="panel-header">
            题材列表
            <button style={st.headerBtn} onClick={newTheme}>新增</button>
          </div>
          <div style={st.filters}>
            <label style={st.check}>
              <input type="checkbox" checked={includeDisabled} onChange={(e) => setIncludeDisabled(e.target.checked)} />
              显示停用
            </label>
          </div>
          <div style={st.themeList}>
            {themes.map((theme) => (
              <button
                key={theme.theme_code}
                style={{
                  ...st.themeItem,
                  ...(theme.theme_code === selectedCode ? st.themeItemActive : {}),
                  opacity: theme.enabled ? 1 : 0.55,
                }}
                onClick={() => void selectTheme(theme.theme_code)}
              >
                <span style={st.themeName}>{theme.theme_name}</span>
                <span style={st.themeMeta}>{theme.priority} · {classLabel(theme.classification)} · {theme.member_count}只</span>
              </button>
            ))}
          </div>
        </section>

        <section className="panel" style={st.main}>
          <div className="panel-header">
            {selected ? '题材详情' : '新增题材'}
            {selected && (
              <span style={st.source}>{selected.source === 'seed' ? '内置' : '手动'} · {selected.enabled ? '启用' : '停用'}</span>
            )}
          </div>
          <div style={st.body}>
            <div style={st.formGrid}>
              <input style={st.input} disabled={Boolean(selected)} placeholder="theme_code(可空)" value={themeForm.theme_code} onChange={(e) => setThemeForm({ ...themeForm, theme_code: e.target.value })} />
              <input style={st.input} placeholder="题材名称" value={themeForm.theme_name} onChange={(e) => setThemeForm({ ...themeForm, theme_name: e.target.value })} />
              <select style={st.input} value={themeForm.classification} onChange={(e) => setThemeForm({ ...themeForm, classification: e.target.value as ThemeClassification })}>
                {classifications.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
              <select style={st.input} value={themeForm.priority} onChange={(e) => setThemeForm({ ...themeForm, priority: e.target.value as ThemePriority })}>
                {priorities.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <input style={{ ...st.input, gridColumn: '1 / -1' }} placeholder="备注" value={themeForm.note} onChange={(e) => setThemeForm({ ...themeForm, note: e.target.value })} />
            </div>
            <div style={st.actions}>
              {selected && <button style={st.ghost} onClick={() => void toggleTheme(selected)}>{selected.enabled ? '停用' : '启用'}</button>}
              {selected && <button style={st.ghostDanger} onClick={() => void removeTheme(selected)}>{selected.source === 'seed' ? '停用内置' : '删除'}</button>}
              <button style={st.btn} onClick={() => void saveTheme()} disabled={saving}>{saving ? '保存中' : '保存题材'}</button>
            </div>

            {selected && (
              <>
                <div style={st.divider} />
                <h2 style={st.h2}>成分股</h2>
                <div style={{ position: 'relative', zIndex: 20, marginBottom: 8 }}>
                  {memberForm.symbol ? (
                    <div style={st.picked}>
                      <span><b>{memberForm.name || memberForm.symbol}</b> <span style={st.code}>{memberForm.symbol}</span></span>
                      <button style={st.ghost} onClick={() => setMemberForm(emptyMember)}>清空</button>
                    </div>
                  ) : (
                    <SymbolSearch market={market} coreOnly={false}
                      placeholder="搜索代码或名称添加成分股"
                      onSelect={(hit: any) => setMemberForm({ ...memberForm, symbol: hit.symbol, name: hit.name || '' })} />
                  )}
                </div>
                <div style={st.memberForm}>
                  <input style={st.input} placeholder="股票代码" value={memberForm.symbol} onChange={(e) => setMemberForm({ ...memberForm, symbol: e.target.value.toUpperCase() })} />
                  <input style={st.input} placeholder="名称" value={memberForm.name} onChange={(e) => setMemberForm({ ...memberForm, name: e.target.value })} />
                  <select style={st.input} value={memberForm.role_hint} onChange={(e) => setMemberForm({ ...memberForm, role_hint: e.target.value })}>
                    {roleHints.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                  <input style={st.input} placeholder="权重" value={memberForm.weight} onChange={(e) => setMemberForm({ ...memberForm, weight: e.target.value })} />
                  <button style={st.btn} onClick={() => void saveMember()}>保存成分股</button>
                </div>

                <table className="data-table">
                  <thead><tr><th>标的</th><th>角色</th><th>权重</th><th>来源</th><th></th></tr></thead>
                  <tbody>
                    {members.length === 0 && <tr><td colSpan={5} style={st.empty}>暂无成分股</td></tr>}
                    {members.map((member) => (
                      <tr key={member.symbol} style={{ opacity: member.enabled ? 1 : 0.5 }}>
                        <td><span style={{ fontWeight: 500, display: 'block' }}>{member.name || member.symbol}</span><span style={st.code}>{member.symbol}</span></td>
                        <td>{member.role_hint || '--'}</td>
                        <td style={st.mono}>{member.weight ?? '--'}</td>
                        <td>{member.source === 'seed' ? '内置' : '手动'}</td>
                        <td>
                          <button style={st.ghost} onClick={() => editMember(member)}>编辑</button>
                          <button style={st.ghostDanger} onClick={() => void removeMember(member)}>{member.source === 'seed' ? '停用' : '删除'}</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  topLine: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 },
  h1: { fontSize: 22, fontWeight: 700, margin: '0 0 4px' },
  h2: { fontSize: 15, margin: '0 0 10px' },
  sub: { color: 'var(--text3)', fontSize: 13, margin: '0 0 18px' },
  stats: { display: 'flex', gap: 8, color: 'var(--text2)', fontSize: 12, whiteSpace: 'nowrap' },
  grid: { display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: 16 },
  side: { minHeight: 560 },
  main: { minHeight: 560 },
  body: { padding: '14px 18px' },
  filters: { padding: '10px 12px', borderBottom: '1px solid var(--border)' },
  check: { display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text2)', fontSize: 12 },
  themeList: { display: 'flex', flexDirection: 'column', gap: 4, padding: 8, maxHeight: 680, overflow: 'auto' },
  themeItem: { textAlign: 'left', border: '1px solid transparent', background: 'transparent', color: 'var(--text)', borderRadius: 6, padding: '9px 10px', cursor: 'pointer' },
  themeItemActive: { background: 'var(--bg3)', borderColor: 'var(--border)' },
  themeName: { display: 'block', fontSize: 13, fontWeight: 600 },
  themeMeta: { display: 'block', marginTop: 3, fontSize: 11, color: 'var(--text3)' },
  headerBtn: { marginLeft: 'auto', background: 'transparent', color: 'var(--accent)', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 9px', cursor: 'pointer', fontSize: 12 },
  source: { marginLeft: 8, color: 'var(--text3)', fontSize: 11, fontWeight: 400 },
  formGrid: { display: 'grid', gridTemplateColumns: '1.2fr 1.2fr 0.9fr 0.6fr', gap: 8 },
  memberForm: { display: 'grid', gridTemplateColumns: '1fr 1fr 0.8fr 0.6fr auto', gap: 8, alignItems: 'center', marginBottom: 12 },
  input: { width: '100%', minWidth: 0, background: 'var(--bg3)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 9px', fontSize: 13, outline: 'none' },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 },
  btn: { background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 6, padding: '8px 14px', cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap' },
  ghost: { background: 'transparent', color: 'var(--accent)', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', cursor: 'pointer', fontSize: 12, marginRight: 6 },
  ghostDanger: { background: 'transparent', color: '#dc2626', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', cursor: 'pointer', fontSize: 12, marginRight: 6 },
  divider: { height: 1, background: 'var(--border)', margin: '18px 0' },
  picked: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '9px 12px', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 },
  code: { fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' },
  mono: { fontFamily: 'monospace' },
  empty: { textAlign: 'center', padding: 24, color: 'var(--text3)' },
  err: { marginBottom: 12, color: '#dc2626', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 6, padding: '8px 10px', fontSize: 13 },
  warn: { marginBottom: 12, color: '#b45309', background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 6, padding: '8px 10px', fontSize: 13 },
}
