from __future__ import annotations

from core.market_rules.events import MarketRuleEvent


def sector_diffusion_label(
    *,
    up_ratio: float | None,
    leader_dominance_pct: float | None,
) -> str:
    if up_ratio is None:
        return "成分缺失"
    if up_ratio >= 0.7 and (leader_dominance_pct is None or leader_dominance_pct < 4):
        return "板块普涨"
    if up_ratio < 0.45 and leader_dominance_pct is not None and leader_dominance_pct >= 4:
        return "龙头独涨"
    if up_ratio < 0.4:
        return "分化偏弱"
    return "正常扩散"


def evaluate_sector_strength(sectors) -> list[MarketRuleEvent]:
    events: list[MarketRuleEvent] = []
    for sector in sectors[:3]:
        if sector.change_pct >= 1.5 or sector.leader_change_pct >= 5:
            detail = (
                f"板块涨幅 {sector.change_pct:.2f}%，"
                f"领涨 {sector.leader_name} {sector.leader_change_pct:.2f}%，"
                f"扩散：{sector.breadth_label}"
            )
            events.append(MarketRuleEvent(
                level="positive",
                category="sector",
                title=f"{sector.name} 走强",
                detail=detail,
                symbols=[s.symbol for s in sector.constituents or []],
                score=sector.change_pct,
            ))
    return events


def evaluate_sector_weakness(sectors) -> list[MarketRuleEvent]:
    events: list[MarketRuleEvent] = []
    for sector in sectors[:3]:
        if sector.change_pct <= -1.5:
            events.append(MarketRuleEvent(
                level="warning",
                category="sector",
                title=f"{sector.name} 走弱",
                detail=(
                    f"板块跌幅 {sector.change_pct:.2f}%，"
                    f"领涨/抗跌标的 {sector.leader_name} {sector.leader_change_pct:.2f}%，"
                    f"扩散：{sector.breadth_label}"
                ),
                symbols=[s.symbol for s in sector.constituents or []],
                score=abs(sector.change_pct),
            ))
    return events

