'use client'

import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchAuction } from '@/lib/market_changes_api'
import { MiniChangeList } from './MiniChangeList'

// 尾盘 (14:55 后) 专项: 拉升 / 跳水 / 抢板 / 炸板。
// A 股尾盘集合竞价无专用源, 用 14:55 后的盘口异动组合而成。
export function ClosingAuctionCard({ market }: { market: string }) {
  const { data, isLoading } = useSWR(
    market === 'ashare' ? ['auction', market] : null,
    () => fetchAuction(market),
    { refreshInterval: 20_000 },
  )
  if (market !== 'ashare') {
    return (
      <section className="panel">
        <div className="panel-header">尾盘竞价</div>
        <div style={st.empty}>仅支持 A 股。</div>
      </section>
    )
  }
  const hasData = data && (data.late_surge.length || data.late_plunge.length
    || data.late_seal.length || data.late_broken.length)
  return (
    <section className="panel">
      <div className="panel-header">
        尾盘竞价异动
        <span style={st.headerMeta}>14:55 后</span>
      </div>
      <div style={st.cols}>
        {isLoading && !data && <div style={st.empty}>加载中</div>}
        {!isLoading && !hasData && <div style={st.empty}>尾盘异动数据未就绪(14:55 后生成)</div>}
        {hasData && (
          <>
            <MiniChangeList title="尾盘拉升" rows={data!.late_surge} tone="up" empty="无" />
            <MiniChangeList title="尾盘跳水" rows={data!.late_plunge} tone="down" empty="无" />
            <MiniChangeList title="尾盘抢板" rows={data!.late_seal} tone="up" empty="无" />
            <MiniChangeList title="尾盘炸板" rows={data!.late_broken} tone="down" empty="无" />
          </>
        )}
      </div>
    </section>
  )
}

const st: Record<string, CSSProperties> = {
  headerMeta: { marginLeft: 'auto', color: 'var(--text3)', fontSize: 12, fontWeight: 400 },
  cols: { display: 'flex', gap: 14, padding: 12, flexWrap: 'wrap' },
  empty: { color: 'var(--text3)', fontSize: 13, padding: 18, textAlign: 'center', width: '100%' },
}
