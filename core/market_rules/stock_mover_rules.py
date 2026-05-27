from __future__ import annotations

from core.market_metrics.market_width import MarketBreadthMetrics
from core.market_rules.events import MarketRuleEvent
from core.services.market_query import RankRow


def evaluate_watchlist_moves(watchlist) -> list[MarketRuleEvent]:
    events: list[MarketRuleEvent] = []
    for item in watchlist:
        pct = item.change_pct
        if pct is None:
            continue
        if pct >= 5:
            events.append(MarketRuleEvent(
                level="positive",
                category="watchlist",
                title=f"{item.name or item.symbol} 大幅上涨",
                detail=f"{item.symbol} 当前涨幅 {pct:.2f}%，所属板块：{', '.join(item.sectors[:3]) or '未匹配'}",
                symbols=[item.symbol],
                score=pct,
            ))
        elif pct <= -5:
            events.append(MarketRuleEvent(
                level="warning",
                category="watchlist",
                title=f"{item.name or item.symbol} 大幅下跌",
                detail=f"{item.symbol} 当前跌幅 {pct:.2f}%，所属板块：{', '.join(item.sectors[:3]) or '未匹配'}",
                symbols=[item.symbol],
                score=abs(pct),
            ))
    return events


def evaluate_limit_moves(
    *,
    breadth: MarketBreadthMetrics,
    top_gainers: list[RankRow],
    top_losers: list[RankRow],
) -> list[MarketRuleEvent]:
    events: list[MarketRuleEvent] = []
    if breadth.total == 0:
        return events
    if top_gainers and top_gainers[0].change_pct >= 9.8:
        events.append(MarketRuleEvent(
            level="positive",
            category="limit",
            title="涨停强度",
            detail=f"全 A 快照中涨停/接近涨停 {breadth.up_limit} 家，强势第一为 {top_gainers[0].name}",
            symbols=[r.symbol for r in top_gainers[:5]],
            score=float(breadth.up_limit),
        ))
    if top_losers and top_losers[0].change_pct <= -9.8:
        events.append(MarketRuleEvent(
            level="warning",
            category="limit",
            title="跌停风险",
            detail=f"全 A 快照中跌停/接近跌停 {breadth.down_limit} 家，弱势第一为 {top_losers[0].name}",
            symbols=[r.symbol for r in top_losers[:5]],
            score=float(breadth.down_limit),
        ))
    return events
