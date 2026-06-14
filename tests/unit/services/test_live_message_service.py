from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.persistence.sqlite_repo import StateRepo
from core.persistence.theme_repo import ThemeRepo
from core.services.live_message_service import LiveMessageService
from core.themes.seed_loader import bootstrap_ashare_seed


@pytest.mark.asyncio
async def test_theme_strength_message_is_deduped(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    await bootstrap_ashare_seed(theme_repo)
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=[])
    svc = LiveMessageService(theme_repo, watchlist)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    base = {"market": "ashare", "ts": ts.isoformat(), "price": 10, "volume": 100}
    assert await svc.handle_quote_tick({**base, "symbol": "300308.SZ", "change_pct": 2.5}) == []
    assert await svc.handle_quote_tick({**base, "symbol": "300502.SZ", "change_pct": 2.0}) == []

    first = await svc.handle_quote_tick({**base, "symbol": "300394.SZ", "change_pct": 1.8})
    assert len(first) == 1
    assert first[0].category == "theme"
    assert first[0].title == "AI算力走强"
    assert first[0].theme_code == "theme:ai_compute"
    assert first[0].payload["up_count"] == 3

    again = await svc.handle_quote_tick({**base, "symbol": "000977.SZ", "change_pct": 1.6})
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

