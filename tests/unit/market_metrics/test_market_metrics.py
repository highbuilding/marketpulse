from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.market_metrics.index_strength import compute_index_strength
from core.market_metrics.market_width import compute_market_width
from core.market_metrics.sector_breadth import compute_sector_breadth
from core.services.market_query import RankRow


def row(symbol: str, pct: float, amount: float = 1_000_000) -> RankRow:
    return RankRow(symbol=symbol, name=symbol, price=10, change_pct=pct, volume=1000, amount=amount)


def test_compute_market_width() -> None:
    metrics = compute_market_width([
        row("000001.SZ", 10.0),
        row("000002.SZ", 1.0),
        row("000003.SZ", 0.0),
        row("000004.SZ", -10.0),
    ])

    assert metrics.total == 4
    assert metrics.advancers == 2
    assert metrics.decliners == 1
    assert metrics.up_limit == 1
    assert metrics.down_limit == 1
    assert metrics.up_ratio == 0.5
    assert metrics.net_width == 1


def test_compute_sector_breadth_detects_leader_dominance() -> None:
    metrics = compute_sector_breadth([
        row("000001.SZ", 8),
        row("000002.SZ", 1),
        row("000003.SZ", -1),
    ])

    assert metrics.total == 3
    assert metrics.up_count == 2
    assert metrics.down_count == 1
    assert metrics.leader_dominance_pct is not None
    assert metrics.leader_dominance_pct > 6


@dataclass(frozen=True)
class IndexFixture:
    symbol: str
    name: str | None
    change_pct: float | None


def test_compute_index_strength() -> None:
    metrics = compute_index_strength([
        IndexFixture("000852.SH", "中证1000", 1.2),
        IndexFixture("000016.SH", "上证50", 0.1),
        IndexFixture("399006.SZ", "创业板指", -0.3),
    ])

    assert metrics.small_vs_large_pct == pytest.approx(1.1)
    assert metrics.growth_vs_large_pct == pytest.approx(-0.4)
    assert metrics.ranking[0]["symbol"] == "000852.SH"
