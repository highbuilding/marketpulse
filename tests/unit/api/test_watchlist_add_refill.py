import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_refill_new_symbol_publishes_all_intervals():
    from apps.api.routes import watchlists
    calls = []

    async def fake_pub(rc, sym, iv, days, *, watchlist=None):
        calls.append(iv)

    with patch("apps.api.routes.symbols._publish_refill_request", fake_pub):
        await watchlists._refill_new_symbol("MRVL", object(), None)

    assert "5m" in calls and "1d" in calls and "4h" in calls
    assert len(calls) >= 5
