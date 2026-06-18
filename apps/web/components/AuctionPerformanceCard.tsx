'use client'

import type { CSSProperties } from 'react'
import useSWR from 'swr'

import { fetchAuction } from '@/lib/market_changes_api'
import { MiniChangeList } from './MiniChangeList'

// 开盘竞价表现: 竞价抢筹 / 竞价砸盘 / 一字板。竞价 tape 逐分钟源不可得,
// 本卡为"竞价结果快照"口径 (竞价异动 + 一字封板)。
export function AuctionPerformanceCard({ market }: { market: string }) {
  const { data, isLoading } = useSWR(
    market === 'ashare' ? ['auction', market] : null,
    () => fetchAuction(market),
    { refreshInterval: 30_000 },
  )
  if (market !== 'ashare') {
    return (
      <section className="panel">
        <div className="panel-header">竞价表现</div>
        <div style={st.empty}>仅支持 A 股。</div>
      </section>
    )
  }
  const hasData = data && (data.auction_up.length || data.auction_down.length || data.one_word_limit.length)
  return (
    <section className="panel">
      <div className="panel-header">
        开盘竞价表现
        <span style={st.headerMeta}>竞价结果快照</span>
      </div>
      <div style={st.cols}>
        {isLoading && !data && <div style={st.empty}>加载中</div>}
        {!isLoading && !hasData && <div style={st.empty}>竞价数据未就绪(9:25 后生成)</div>}
        {hasData && (
          <>
            <MiniChangeList title="竞价抢筹" rows={data!.auction_up} tone="up" empty="无竞价上涨" />
            <MiniChangeList title="竞价砸盘" rows={data!.auction_down} tone="down" empty="无竞价下跌" />
            <MiniChangeList title="一字涨停" rows={data!.one_word_limit} tone="up" empty="无一字板" />
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
