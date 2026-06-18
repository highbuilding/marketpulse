'use client'

import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'

import { useMarket } from '@/lib/market-context'
import { ashareBoardPhase, ashareBoardPhaseLabel } from '@/lib/markets'
import { MarketPhaseBar } from '@/components/MarketPhaseBar'
import { IndexBar } from '@/components/IndexBar'
import { KeyAlertsStrip } from '@/components/KeyAlertsStrip'
import { AuctionPerformanceCard } from '@/components/AuctionPerformanceCard'
import { ClosingAuctionCard } from '@/components/ClosingAuctionCard'
import { PreviousLimitCard } from '@/components/PreviousLimitCard'
import { ChangesFeed } from '@/components/ChangesFeed'
import { BoardChangesCard } from '@/components/BoardChangesCard'
import { IntradayRoundsTimeline } from '@/components/IntradayRoundsTimeline'
import { ThemeTrendCard } from '@/components/ThemeTrendCard'

// 时段驱动盘面: 竞价 → 开盘 → 盘中异动 + 30min 走势轮 → 尾盘。
// 实盘消息列表已撤出 (数据仍落库供盘后回放); 高优先级提示走 KeyAlertsStrip。
export default function MarketPage() {
  const { market, marketLabel } = useMarket()
  const [phase, setPhase] = useState(() => ashareBoardPhase())
  useEffect(() => {
    const t = setInterval(() => setPhase(ashareBoardPhase()), 20_000)
    return () => clearInterval(t)
  }, [])

  const isAshare = market === 'ashare'
  // 竞价/开盘时段竞价卡置顶; 尾盘时段尾盘卡置顶。
  const auctionFirst = phase === 'pre' || phase === 'auction' || phase === 'open'
  const closingFirst = phase === 'closing' || phase === 'post'

  return (
    <div style={st.page}>
      <div>
        <h1 style={st.h1}>{marketLabel.flag} {marketLabel.name} 盘面</h1>
        <p style={st.sub}>
          {marketLabel.sub} · 时段驱动盘面监控
          {isAshare && <span style={st.phaseTag}>当前: {ashareBoardPhaseLabel(phase)}</span>}
        </p>
      </div>

      {!isAshare && (
        <div style={st.notice}>盘面监控目前仅支持 A 股。其他市场后续接入。</div>
      )}

      <KeyAlertsStrip market={market} />
      <MarketPhaseBar />
      <IndexBar market={market} />

      {/* 竞价/尾盘卡按时段置顶 */}
      {auctionFirst && <AuctionPerformanceCard market={market} />}
      {closingFirst && <ClosingAuctionCard market={market} />}

      <div style={st.grid}>
        <div style={st.colMain}>
          <ChangesFeed market={market} />
          <IntradayRoundsTimeline market={market} />
        </div>
        <div style={st.colSide}>
          <PreviousLimitCard market={market} />
          <ThemeTrendCard market={market} />
          <BoardChangesCard market={market} />
        </div>
      </div>

      {/* 非置顶时段, 竞价/尾盘卡放底部备查 */}
      {!auctionFirst && <AuctionPerformanceCard market={market} />}
      {!closingFirst && <ClosingAuctionCard market={market} />}
    </div>
  )
}

const st: Record<string, CSSProperties> = {
  page: { padding: 20, display: 'flex', flexDirection: 'column', gap: 14 },
  h1: { fontSize: 20, fontWeight: 700, marginBottom: 4 },
  sub: { color: 'var(--text2)', fontSize: 13 },
  phaseTag: { marginLeft: 12, color: 'var(--accent)', fontWeight: 600 },
  notice: { padding: 12, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--bg2)', color: 'var(--text2)', fontSize: 13 },
  grid: { display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)', gap: 14, alignItems: 'start' },
  colMain: { display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 },
  colSide: { display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 },
}
