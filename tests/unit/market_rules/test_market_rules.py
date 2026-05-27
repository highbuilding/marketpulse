from __future__ import annotations

from dataclasses import dataclass

from core.market_metrics.index_strength import IndexStrengthMetrics
from core.market_metrics.market_width import MarketBreadthMetrics
from core.market_rules.index_style_rules import evaluate_index_style
from core.market_rules.market_width_rules import evaluate_market_width
from core.market_rules.sector_diffusion_rules import sector_diffusion_label


def breadth(**kwargs) -> MarketBreadthMetrics:
    base = {
        "total": 5000,
        "advancers": 1000,
        "decliners": 3500,
        "flat": 500,
        "up_limit": 10,
        "down_limit": 2,
        "total_amount": 1_000_000_000,
        "up_ratio": 0.2,
        "down_ratio": 0.7,
        "net_width": -2500,
    }
    base.update(kwargs)
    return MarketBreadthMetrics(**base)


def test_market_width_rules_emit_weak_and_fast_deterioration() -> None:
    events = evaluate_market_width(
        breadth(decliners=3600, down_ratio=0.72),
        previous=breadth(decliners=3000, down_ratio=0.6),
    )

    assert {event.category for event in events} == {"breadth", "breadth_change"}


@dataclass(frozen=True)
class SectorFixture:
    change_pct: float
    leader_change_pct: float
    breadth_label: str
    name: str = "测试板块"
    leader_name: str = "测试股"
    constituents: list | None = None


def test_sector_diffusion_labels() -> None:
    assert sector_diffusion_label(up_ratio=0.8, leader_dominance_pct=1.2) == "板块普涨"
    assert sector_diffusion_label(up_ratio=0.3, leader_dominance_pct=5.0) == "龙头独涨"
    assert sector_diffusion_label(up_ratio=None, leader_dominance_pct=None) == "成分缺失"


def test_index_style_rules_emit_small_cap_event() -> None:
    events = evaluate_index_style(IndexStrengthMetrics(
        ranking=[],
        small_vs_large_pct=1.1,
        growth_vs_large_pct=0.2,
    ))

    assert len(events) == 1
    assert events[0].category == "style"
    assert events[0].level == "positive"

