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
    svc, _ = await _service_with_repo(tmp_path, watch_symbols=watch_symbols)
    return svc


async def _service_with_repo(
    tmp_path: Path,
    *,
    watch_symbols: list[str] | None = None,
) -> tuple[LiveMessageService, ThemeRepo]:
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
    return LiveMessageService(theme_repo, watchlist), theme_repo


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


async def _bar(
    svc: LiveMessageService,
    symbol: str,
    volume: int,
    ts: datetime,
    *,
    open_price: float = 10,
    close_price: float = 10.5,
) -> list:
    return await svc.handle_bar_updated({
        "market": "ashare",
        "symbol": symbol,
        "interval": "5m",
        "ts": ts.isoformat(),
        "open": open_price,
        "high": max(open_price, close_price),
        "low": min(open_price, close_price),
        "close": close_price,
        "volume": volume,
        "final": True,
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
async def test_theme_eval_persists_snapshot_and_state(tmp_path: Path):
    svc, theme_repo = await _service_with_repo(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 2.5, ts)
    await _tick(svc, "000002.SZ", 2.0, ts)
    await _tick(svc, "000003.SZ", 1.8, ts)

    snapshots = await theme_repo.list_recent_snapshots("ashare", limit=5)
    states = await theme_repo.list_states("ashare")

    assert snapshots[0].theme_code == "theme:test"
    assert snapshots[0].ts == ts.replace(minute=35, second=0, microsecond=0)
    assert snapshots[0].up_ratio == 1.0
    assert snapshots[0].leader_symbols == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert states[0].theme_code == "theme:test"
    assert states[0].state == "strength"
    assert states[0].evidence["up_count"] == 3


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
async def test_watchlist_volume_spike_message(tmp_path: Path):
    svc = await _service(tmp_path, watch_symbols=["000001.SZ"])
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    for i in range(4):
        assert await _bar(svc, "000001.SZ", 1000, ts.replace(minute=35 + i * 5)) == []
    messages = await _bar(svc, "000001.SZ", 3000, ts.replace(minute=55))

    assert len(messages) == 1
    assert messages[0].category == "watchlist"
    assert messages[0].title == "自选股 000001.SZ 5m明显放量"
    assert messages[0].payload["volume_ratio"] == 3.0


@pytest.mark.asyncio
async def test_volume_spike_ignores_unrelated_symbol(tmp_path: Path):
    svc = await _service(tmp_path, watch_symbols=[])
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    for i in range(4):
        assert await _bar(svc, "600519.SH", 1000, ts.replace(minute=35 + i * 5)) == []
    assert await _bar(svc, "600519.SH", 4000, ts.replace(minute=55)) == []


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


@pytest.mark.asyncio
async def test_index_pulse_weak_message_from_core_indices(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SH", -0.4, ts) == []
    assert await _tick(svc, "399001.SZ", -0.5, ts) == []
    assert await _tick(svc, "399006.SZ", -0.7, ts) == []
    assert await _tick(svc, "000300.SH", -0.3, ts) == []
    messages = await _tick(svc, "000905.SH", -0.8, ts)

    assert [m.title for m in messages] == ["大盘脉搏偏弱"]
    assert messages[0].category == "index"
    assert messages[0].payload["down_count"] == 5
    assert messages[0].payload["state"] == "weak"


@pytest.mark.asyncio
async def test_index_style_large_defense_risk(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SH", 0.1, ts) == []
    assert await _tick(svc, "000300.SH", 0.2, ts) == []
    assert await _tick(svc, "399006.SZ", -0.4, ts) == []
    assert await _tick(svc, "000016.SH", 0.5, ts) == []
    messages = await _tick(svc, "000852.SH", -0.7, ts)

    assert [m.title for m in messages] == ["权重护盘但小票走弱"]
    assert messages[0].category == "risk"
    assert messages[0].payload["small_vs_large_pct"] == pytest.approx(-1.2)
