from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.domain.models import ThemeConstituent, ThemeDefinition
from core.persistence.sqlite_repo import StateRepo
from core.persistence.theme_repo import ThemeRepo
from core.services.live_message_service import LiveMessageService


async def _service(tmp_path: Path, *, watch_symbols: list[str] | None = None) -> LiveMessageService:
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    await theme_repo.seed_definitions(
        [
            ThemeDefinition(
                market="ashare",
                theme_code="theme:test",
                theme_name="测试题材",
                classification="theme",
                priority="P0",
                source="seed",
            ),
        ],
        [
            ThemeConstituent("ashare", "theme:test", "000001.SZ", "核心A", "leader", 10, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000002.SZ", "核心B", "core", 9, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000003.SZ", "跟随C", "follower", 5, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000004.SZ", "跟随D", "follower", 4, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000005.SZ", "跟随E", "watch", 3, source="seed"),
        ],
    )
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=watch_symbols or [])
    return LiveMessageService(theme_repo, watchlist)


async def _tick(
    svc: LiveMessageService,
    symbol: str,
    change_pct: float,
    ts: datetime,
) -> list:
    return await svc.handle_quote_tick({
        "market": "ashare",
        "symbol": symbol,
        "ts": ts.isoformat(),
        "price": 10,
        "change_pct": change_pct,
        "volume": 100,
    })


@pytest.mark.asyncio
async def test_theme_strength_message_is_deduped(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 2.5, ts) == []
    assert await _tick(svc, "000002.SZ", 2.0, ts) == []

    first = await _tick(svc, "000003.SZ", 1.8, ts)
    assert len(first) == 1
    assert first[0].category == "theme"
    assert first[0].title == "测试题材走强"
    assert first[0].theme_code == "theme:test"
    assert first[0].payload["up_count"] == 3
    assert first[0].payload["core_up_count"] == 2

    again = await _tick(svc, "000004.SZ", 1.6, ts)
    assert again == []


@pytest.mark.asyncio
async def test_watchlist_flip_message(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=["600519.SH"])
    svc = LiveMessageService(theme_repo, watchlist)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    base = {"market": "ashare", "ts": ts.isoformat(), "symbol": "600519.SH", "price": 100, "volume": 100}
    assert await svc.handle_quote_tick({**base, "change_pct": -0.2}) == []
    messages = await svc.handle_quote_tick({**base, "change_pct": 0.1})
    assert len(messages) == 1
    assert messages[0].category == "watchlist"
    assert messages[0].title == "自选股 600519.SH 翻红"


@pytest.mark.asyncio
async def test_theme_leader_switch_message(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 2.5, ts) == []
    assert await _tick(svc, "000002.SZ", 2.0, ts) == []
    assert [m.title for m in await _tick(svc, "000003.SZ", 1.5, ts)] == ["测试题材走强"]

    messages = await _tick(svc, "000002.SZ", 4.0, ts)
    assert [m.title for m in messages] == ["测试题材核心股切换"]
    assert messages[0].payload["prev_leader"] == "000001.SZ"


@pytest.mark.asyncio
async def test_theme_quality_risk_messages(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 0.2, ts) == []
    assert await _tick(svc, "000002.SZ", -0.1, ts) == []
    assert await _tick(svc, "000003.SZ", 2.5, ts) == []
    messages = await _tick(svc, "000004.SZ", 1.5, ts)

    titles = {m.title for m in messages}
    assert "测试题材走强" in titles
    assert "测试题材走强质量一般" in titles
    risk = next(m for m in messages if m.title == "测试题材走强质量一般")
    assert risk.category == "risk"
    assert risk.payload["core_up_count"] == 1


@pytest.mark.asyncio
async def test_theme_single_leader_risk(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 4.5, ts) == []
    assert await _tick(svc, "000002.SZ", 1.0, ts) == []
    messages = await _tick(svc, "000003.SZ", -0.2, ts)

    assert [m.title for m in messages] == ["测试题材异动偏单点"]
    assert messages[0].category == "risk"


@pytest.mark.asyncio
async def test_theme_strength_fade_risk(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 1.8, ts)
    await _tick(svc, "000002.SZ", 1.7, ts)
    await _tick(svc, "000003.SZ", 1.6, ts)
    await _tick(svc, "000004.SZ", 1.5, ts)

    assert await _tick(svc, "000003.SZ", -0.2, ts) == []
    messages = await _tick(svc, "000004.SZ", -0.3, ts)
    assert [m.title for m in messages] == ["测试题材强度回落"]
    assert messages[0].payload["peak_up_count"] == 4


@pytest.mark.asyncio
async def test_watchlist_against_theme_risk(tmp_path: Path):
    svc = await _service(tmp_path, watch_symbols=["000005.SZ"])
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 2.0, ts)
    await _tick(svc, "000002.SZ", 1.8, ts)
    await _tick(svc, "000003.SZ", 1.5, ts)
    messages = await _tick(svc, "000005.SZ", -1.2, ts)

    assert [m.title for m in messages] == ["自选股 000005.SZ 逆测试题材走弱"]
    assert messages[0].category == "risk"
