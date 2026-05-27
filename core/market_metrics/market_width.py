from __future__ import annotations

from dataclasses import dataclass

from core.services.market_query import RankRow


@dataclass(frozen=True, slots=True)
class MarketBreadthMetrics:
    total: int
    advancers: int
    decliners: int
    flat: int
    up_limit: int
    down_limit: int
    total_amount: float
    up_ratio: float
    down_ratio: float
    net_width: int


def compute_market_width(rows: list[RankRow]) -> MarketBreadthMetrics:
    total = len(rows)
    advancers = sum(1 for r in rows if r.change_pct > 0)
    decliners = sum(1 for r in rows if r.change_pct < 0)
    up_limit = sum(1 for r in rows if r.change_pct >= 9.8)
    down_limit = sum(1 for r in rows if r.change_pct <= -9.8)
    total_amount = sum(r.amount for r in rows)
    return MarketBreadthMetrics(
        total=total,
        advancers=advancers,
        decliners=decliners,
        flat=max(total - advancers - decliners, 0),
        up_limit=up_limit,
        down_limit=down_limit,
        total_amount=total_amount,
        up_ratio=(advancers / total) if total else 0.0,
        down_ratio=(decliners / total) if total else 0.0,
        net_width=advancers - decliners,
    )

