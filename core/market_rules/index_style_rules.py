from __future__ import annotations

from core.market_metrics.index_strength import IndexStrengthMetrics
from core.market_rules.events import MarketRuleEvent


def evaluate_index_style(metrics: IndexStrengthMetrics) -> list[MarketRuleEvent]:
    events: list[MarketRuleEvent] = []
    if metrics.small_vs_large_pct is not None and abs(metrics.small_vs_large_pct) >= 0.8:
        positive = metrics.small_vs_large_pct > 0
        events.append(MarketRuleEvent(
            level="positive" if positive else "warning",
            category="style",
            title="小盘风格相对占优" if positive else "上证50相对占优",
            detail=f"中证1000 相对上证50强弱差 {metrics.small_vs_large_pct:+.2f}%",
            symbols=["000852.SH", "000016.SH"],
            score=abs(metrics.small_vs_large_pct),
        ))
    if metrics.growth_vs_large_pct is not None and abs(metrics.growth_vs_large_pct) >= 0.8:
        positive = metrics.growth_vs_large_pct > 0
        events.append(MarketRuleEvent(
            level="positive" if positive else "warning",
            category="style",
            title="成长风格相对占优" if positive else "上证50相对占优",
            detail=f"创业板指相对上证50强弱差 {metrics.growth_vs_large_pct:+.2f}%",
            symbols=["399006.SZ", "000016.SH"],
            score=abs(metrics.growth_vs_large_pct),
        ))
    return events
