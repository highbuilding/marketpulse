import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_refill_core_symbol_publishes_all_intervals():
    """CORE 名单内标的: refill 发全周期 (5m + SIGNAL_INTERVALS)。"""
    from apps.api.routes import watchlists
    calls = []

    async def fake_pub(rc, sym, iv, days, *, watchlist=None):
        calls.append(iv)

    # AAPL ∈ CORE_SYMBOLS["us"], 应触发 refill
    with patch("apps.api.routes.symbols._publish_refill_request", fake_pub):
        await watchlists._refill_new_symbol("AAPL", object(), None)

    assert "5m" in calls and "1d" in calls and "4h" in calls
    assert len(calls) >= 5


@pytest.mark.asyncio
async def test_refill_non_core_symbol_skipped():
    """非 CORE 标的: 前端不可触发名单外采集 (commit 54e6e30 白名单)。"""
    from apps.api.routes import watchlists
    calls = []

    async def fake_pub(rc, sym, iv, days, *, watchlist=None):
        calls.append(iv)

    # MRVL ∉ CORE → 直接 skip, 不发任何 refill
    with patch("apps.api.routes.symbols._publish_refill_request", fake_pub):
        await watchlists._refill_new_symbol("MRVL", object(), None)

    assert calls == []
