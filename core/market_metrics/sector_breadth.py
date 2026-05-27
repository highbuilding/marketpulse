from __future__ import annotations

from dataclasses import dataclass

from core.services.market_query import RankRow


@dataclass(frozen=True, slots=True)
class SectorBreadthMetrics:
    total: int
    up_count: int
    down_count: int
    up_ratio: float
    avg_change_pct: float | None
    leader_dominance_pct: float | None


def compute_sector_breadth(constituents: list[RankRow]) -> SectorBreadthMetrics:
    total = len(constituents)
    if total == 0:
        return SectorBreadthMetrics(
            total=0,
            up_count=0,
            down_count=0,
            up_ratio=0.0,
            avg_change_pct=None,
            leader_dominance_pct=None,
        )
    up_count = sum(1 for row in constituents if row.change_pct > 0)
    down_count = sum(1 for row in constituents if row.change_pct < 0)
    avg_change_pct = sum(row.change_pct for row in constituents) / total
    top = max(row.change_pct for row in constituents)
    rest = [row.change_pct for row in constituents if row.change_pct != top]
    rest_avg = sum(rest) / len(rest) if rest else avg_change_pct
    return SectorBreadthMetrics(
        total=total,
        up_count=up_count,
        down_count=down_count,
        up_ratio=up_count / total,
        avg_change_pct=avg_change_pct,
        leader_dominance_pct=top - rest_avg,
    )

