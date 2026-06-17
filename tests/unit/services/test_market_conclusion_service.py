from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.domain.models import LiveMessage, ThemeSnapshot
from core.services.market_conclusion_service import MarketConclusionService


class _LiveRepo:
    def __init__(self, rows):
        self.rows = rows

    async def list_window(self, *args, **kwargs):
        return self.rows

    async def list_window_asc(self, *args, **kwargs):
        return self.rows


class _ThemeRepo:
    def __init__(self, rows):
        self.rows = rows

    async def list_snapshots_window(self, *args, **kwargs):
        return self.rows


class _LimitRepo:
    def __init__(self, summary):
        self.summary = summary

    async def summary_by_date(self, *args, **kwargs):
        return self.summary


def _msg(
    *,
    id_: str,
    category: str,
    level: str,
    title: str,
    body: str = "",
    theme_code: str | None = None,
    dedupe_key: str | None = None,
) -> LiveMessage:
    return LiveMessage(
        id=id_,
        market="ashare",
        ts=datetime(2026, 6, 17, 2, 0, tzinfo=timezone.utc),
        level=level,
        category=category,
        title=title,
        body=body,
        source_event="test",
        dedupe_key=dedupe_key or id_,
        theme_code=theme_code,
    )


def _snapshot(
    *,
    theme: str,
    ts_minute: int,
    up_ratio: float,
    pct_change: float,
    pct_change_5m: float,
    amount_ratio: float,
    limit_up_count: int,
    divergence_score: float = 0.0,
) -> ThemeSnapshot:
    return ThemeSnapshot(
        market="ashare",
        theme_code=f"theme:{theme}",
        theme_name=theme,
        classification="theme",
        ts=datetime(2026, 6, 17, 2, ts_minute, tzinfo=timezone.utc),
        up_ratio=up_ratio,
        pct_change=pct_change,
        pct_change_5m=pct_change_5m,
        amount_ratio=amount_ratio,
        limit_up_count=limit_up_count,
        divergence_score=divergence_score,
    )


@pytest.mark.asyncio
async def test_intraday_conclusion_computes_risk_and_signal_balance():
    svc = MarketConclusionService(
        _LiveRepo([
            # 收紧口径: 只有"转弱"(dedupe :weakness) + critical 算风险, 分歧/异动不算。
            # 4个题材转弱 + 1 critical → intensity = 4 + 2 = 6 → 风险偏高。
            _msg(id_="r1", category="risk", level="critical", title="炸板风险出现", theme_code="theme:a"),
            _msg(id_="w1", category="theme", level="warning", title="题材b转弱", theme_code="theme:b", dedupe_key="theme:b:weakness"),
            _msg(id_="w2", category="theme", level="warning", title="题材c转弱", theme_code="theme:c", dedupe_key="theme:c:weakness"),
            _msg(id_="w3", category="theme", level="warning", title="题材d转弱", theme_code="theme:d", dedupe_key="theme:d:weakness"),
            _msg(id_="w4", category="theme", level="warning", title="题材e转弱", theme_code="theme:e", dedupe_key="theme:e:weakness"),
            # 分歧/异动不计入风险 (常态噪音)
            _msg(id_="d1", category="risk", level="warning", title="题材f分歧", theme_code="theme:f", dedupe_key="risk:theme:f:divergence"),
            _msg(id_="s1", category="signal", level="watch", title="某标的触发 CD 买入信号"),
            _msg(id_="s2", category="signal", level="watch", title="某标的触发 CD 卖出信号"),
            _msg(id_="s3", category="signal", level="watch", title="某标的触发 CD 买入信号"),
        ]),
        _ThemeRepo([]),
    )

    out = await svc.intraday("ashare", minutes=60)
    sections = {s.key: s for s in out.sections}

    # 4 转弱题材 + 1 critical → intensity = 4 + 2 = 6 → 风险偏高 (>=4); 分歧不计
    assert sections["risk_state"].label == "风险偏高"
    assert sections["risk_state"].score == pytest.approx(6.0)
    # CD 信号被剔除出风险, 但 signal_state 独立统计买卖平衡
    assert sections["signal_state"].label == "买入信号占优"
    assert sections["signal_state"].score == 1


@pytest.mark.asyncio
async def test_intraday_conclusion_ranks_theme_heat():
    svc = MarketConclusionService(
        _LiveRepo([]),
        _ThemeRepo([
            _snapshot(theme="机器人", ts_minute=0, up_ratio=0.45, pct_change=1.2,
                      pct_change_5m=0.3, amount_ratio=1.1, limit_up_count=1),
            _snapshot(theme="机器人", ts_minute=5, up_ratio=0.82, pct_change=4.5,
                      pct_change_5m=1.8, amount_ratio=1.8, limit_up_count=3),
            _snapshot(theme="算力", ts_minute=5, up_ratio=0.50, pct_change=2.0,
                      pct_change_5m=0.2, amount_ratio=1.0, limit_up_count=0),
        ]),
        None,
    )

    out = await svc.intraday("ashare", minutes=60)
    theme = next(s for s in out.sections if s.key == "theme_state")

    assert theme.label == "主线扩散"
    assert theme.evidence["top_themes"][0]["theme_name"] == "机器人"
    assert theme.evidence["top_themes"][0]["momentum"] == pytest.approx(0.37)


@pytest.mark.asyncio
async def test_daily_review_builds_summary_and_next_watch():
    svc = MarketConclusionService(
        _LiveRepo([
            _msg(id_="i1", category="index", level="watch", title="核心指数共振走强"),
            _msg(id_="t1", category="theme", level="watch", title="题材进入扩散"),
            _msg(id_="s1", category="signal", level="watch", title="某标的触发 CD 买入信号"),
        ]),
        _ThemeRepo([
            _snapshot(theme="机器人", ts_minute=0, up_ratio=0.30, pct_change=1.0,
                      pct_change_5m=0.1, amount_ratio=1.0, limit_up_count=0),
            _snapshot(theme="机器人", ts_minute=10, up_ratio=0.88, pct_change=5.0,
                      pct_change_5m=2.0, amount_ratio=1.8, limit_up_count=3),
        ]),
    )

    out = await svc.daily_review("ashare", trade_date="2026-06-17")
    sections = {s.key: s for s in out.sections}

    assert out.trade_date == "2026-06-17"
    assert "题材:主线明确" in out.summary
    assert sections["daily_theme"].label == "主线明确"
    assert any("低位容量趋势观察池" in item for item in out.next_watch)
    assert any("真实涨停/连板/炸板池" in gap for gap in out.data_gaps)


@pytest.mark.asyncio
async def test_intraday_conclusion_uses_limit_pool_summary():
    svc = MarketConclusionService(
        _LiveRepo([]),
        _ThemeRepo([]),
        _LimitRepo({
            "limit_up_count": 58,
            "broken_count": 12,
            "down_limit_count": 1,
            "break_rate": 12 / 70,
            "max_ladder_height": 6,
            "ladder_counts": {1: 35, 2: 12, 3: 6, 4: 3, 5: 1, 6: 1},
            "ladder_break_count": 0,
            "sample_symbols": {
                "limit_up": ["600110.SH"],
                "broken_limit": ["000777.SZ"],
                "down_limit": ["603586.SH"],
            },
        }),
    )

    out = await svc.intraday("ashare", minutes=60)
    sections = {s.key: s for s in out.sections}

    assert sections["limit_structure"].label == "连板生态较强"
    assert sections["limit_structure"].evidence["limit_up_count"] == 58
    assert not any("真实涨停" in gap for gap in out.data_gaps)
