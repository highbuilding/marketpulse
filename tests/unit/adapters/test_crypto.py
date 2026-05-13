from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from core.adapters.crypto import CryptoAdapter


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_uses_coingecko_backup():
    respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
        return_value=httpx.Response(200, json={
            "bitcoin": {"usd": 68000.5, "usd_24h_change": 2.1, "usd_24h_vol": 1.2e10},
            "ethereum": {"usd": 3500.0, "usd_24h_change": -0.5, "usd_24h_vol": 5e9},
        })
    )
    adapter = CryptoAdapter()
    quotes = await adapter.fetch_snapshot(["BTC-USDT", "ETH-USDT"])
    assert len(quotes) == 2
    btc = next(q for q in quotes if q.symbol == "BTC-USDT")
    assert btc.price == Decimal("68000.5")
    assert btc.change_pct == pytest.approx(2.1)
    assert btc.source == "coingecko"


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_handles_coingecko_429():
    respx.get("https://api.coingecko.com/api/v3/simple/price").mock(
        return_value=httpx.Response(429)
    )
    adapter = CryptoAdapter()
    with pytest.raises(Exception):
        await adapter.fetch_snapshot(["BTC-USDT"])


def test_symbol_to_coingecko_id():
    adapter = CryptoAdapter()
    assert adapter._to_cg_id("BTC-USDT") == "bitcoin"
    assert adapter._to_cg_id("ETH-USDT") == "ethereum"
    assert adapter._to_cg_id("UNKNOWN-USDT") is None


@pytest.mark.asyncio
async def test_health_ok_when_ws_not_started():
    adapter = CryptoAdapter()
    h = await adapter.health()
    assert h.name == "crypto"
    assert h.state in {"ok", "degraded"}
