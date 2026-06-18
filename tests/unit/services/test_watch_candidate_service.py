from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.models import Bar, ThemeConstituent, ThemeDefinition, ThemeSnapshot
from core.persistence.candidate_repo import CandidateRepo
from core.persistence.sqlite_repo import StateRepo
from core.services.watch_candidate_service import WatchCandidateService


class _BarRepo:
    def fetch_history(self, market, symbol, start, end, interval="1d", *, closed_only=False):
        base = datetime(2026, 1, 2, tzinfo=timezone.utc)
        closes = [10 + i * 0.02 for i in range(60)] + [11.0, 11.05, 11.1, 11.12]
        out = []
        for i, c in enumerate(closes):
            out.append(Bar(
                market=market,
                symbol=symbol,
                ts=base + timedelta(days=i),
                open=Decimal(str(c * 0.99)),
                high=Decimal(str(c * 1.03)),
                low=Decimal(str(c * 0.98)),
                close=Decimal(str(c)),
                volume=1000,
                amount=800_000_000,
                interval=interval,
            ))
        return out


class _ThemeRepo:
    async def list_definitions(self, *args, **kwargs):
        return [
            ThemeDefinition(
                market="ashare", theme_code="robot", theme_name="机器人",
                classification="theme"),
        ]

    async def list_static_constituents(self, *args, **kwargs):
        return [
            ThemeConstituent(
                market="ashare", theme_code="robot", symbol="002415.SZ",
                name="海康威视"),
        ]

    async def list_snapshots_window(self, *args, **kwargs):
        return [
            ThemeSnapshot(
                market="ashare", theme_code="robot", theme_name="机器人",
                classification="theme", ts=datetime(2026, 6, 17, 7, 0, tzinfo=timezone.utc),
                up_ratio=0.8, pct_change=4.0, pct_change_5m=1.0,
                amount_ratio=1.5, limit_up_count=2, divergence_score=0),
        ]


class _LimitPool:
    async def summary_by_date(self, *args, **kwargs):
        return {"break_rate": 0.1, "down_limit_count": 0}


@pytest.mark.asyncio
async def test_generate_low_position_capacity_candidates(tmp_path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    svc = WatchCandidateService(
        CandidateRepo(str(db)),
        themes=_ThemeRepo(),
        bar_repo=_BarRepo(),
        limit_pool=_LimitPool(),
    )

    rows = await svc.generate_low_position_capacity_trend(
        "ashare", trade_date="2026-06-17")

    assert len(rows) == 1
    assert rows[0].candidate_type == "low_position_capacity_trend"
    assert rows[0].decision in {"observe", "wait_confirm"}
    assert rows[0].evidence["formula_version"] == "low-position-capacity-v1"
