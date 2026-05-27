import type { ResponseMeta } from '../lib/types'

interface Props {
  meta?: ResponseMeta
  className?: string
}

function formatAge(s?: number): string {
  if (s == null) return ''
  if (s < 60) return `${Math.round(s)} 秒`
  if (s < 3600) return `${Math.round(s / 60)} 分钟`
  return `${(s / 3600).toFixed(1)} 小时`
}

export function StaleBadge({ meta, className }: Props) {
  if (!meta || (!meta.stale && !meta.partial)) return null
  if (meta.partial && !meta.stale) {
    return (
      <span
        className={`inline-block px-2 py-0.5 text-xs rounded bg-yellow-200 text-yellow-900 ${className ?? ''}`}
        title="部分字段缺失,展示已有数据"
      >
        部分字段缺失
      </span>
    )
  }
  const age = meta.data_age_seconds ? `(${formatAge(meta.data_age_seconds)} 前)` : ''
  return (
    <span
      className={`inline-block px-2 py-0.5 text-xs rounded bg-gray-300 text-gray-800 ${className ?? ''}`}
      title={meta.reason ? `原因: ${meta.reason}` : '数据延迟'}
    >
      数据延迟 {age}
    </span>
  )
}
