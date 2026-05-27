from __future__ import annotations

from core.market_metrics.market_width import MarketBreadthMetrics
from core.market_rules.events import MarketRuleEvent


def evaluate_market_width(
    breadth: MarketBreadthMetrics,
    *,
    previous: MarketBreadthMetrics | None = None,
) -> list[MarketRuleEvent]:
    events: list[MarketRuleEvent] = []
    if breadth.total == 0:
        return events

    if breadth.down_ratio >= 0.65:
        events.append(MarketRuleEvent(
            level="warning",
            category="breadth",
            title="市场宽度偏弱",
            detail=f"全 A 下跌 {breadth.decliners} 家，占比 {breadth.down_ratio:.0%}",
            symbols=[],
            score=breadth.down_ratio,
        ))
    elif breadth.up_ratio >= 0.65:
        events.append(MarketRuleEvent(
            level="positive",
            category="breadth",
            title="市场宽度偏强",
            detail=f"全 A 上涨 {breadth.advancers} 家，占比 {breadth.up_ratio:.0%}",
            symbols=[],
            score=breadth.up_ratio,
        ))

    if previous and previous.total:
        decliner_delta = breadth.decliners - previous.decliners
        advancer_delta = breadth.advancers - previous.advancers
        if decliner_delta >= 500:
            events.append(MarketRuleEvent(
                level="warning",
                category="breadth_change",
                title="市场宽度快速恶化",
                detail=f"下跌家数较上一快照增加 {decliner_delta} 家",
                symbols=[],
                score=float(decliner_delta),
            ))
        elif advancer_delta >= 500:
            events.append(MarketRuleEvent(
                level="positive",
                category="breadth_change",
                title="市场宽度快速改善",
                detail=f"上涨家数较上一快照增加 {advancer_delta} 家",
                symbols=[],
                score=float(advancer_delta),
            ))
    return events

