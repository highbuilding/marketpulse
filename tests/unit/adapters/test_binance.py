"""Tests for core.adapters.binance."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from core.adapters.binance import BinanceAdapter, _from_binance, _to_binance


def test_symbol_mapping():
    assert _to_binance("BTC-USDT") == "BTCUSDT"
    assert _to_binance("eth-usdt") == "ETHUSDT"
    assert _from_binance("BTCUSDT") == "BTC-USDT"
    assert _from_binance("ETHUSDC") == "ETH-USDC"


def test_parse_kline_open_ts():
    # openTime 1700000000000 ms (5m bar)
    # crypto 例外: bar.ts 用 openTime (与币安 / TradingView K 线对齐)
    row = [
        1700000000000,
        "1.0",
        "2.0",
        "0.5",
        "1.5",
        "100",
        1700000299999,
        "0",
        0,
        "0",
        "0",
        "0",
    ]
    bar = BinanceAdapter._parse_kline("BTC-USDT", "5m", row)
    expected_ts = datetime.fromtimestamp(1700000000000 / 1000, tz=timezone.utc)
    assert bar.ts == expected_ts
    assert bar.open == Decimal("1.0")
    assert bar.high == Decimal("2.0")
    assert bar.low == Decimal("0.5")
    assert bar.close == Decimal("1.5")
    assert bar.volume == 100
    assert bar.interval == "5m"
    assert bar.market == "crypto"
    assert bar.symbol == "BTC-USDT"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_klines_paged_single_page():
    adapter = BinanceAdapter()
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=httpx.Response(
            200,
            json=[
                [
                    1700000000000,
                    "1.0",
                    "2.0",
                    "0.5",
                    "1.5",
                    "100",
                    1700000299999,
                    "0",
                    0,
                    "0",
                    "0",
                    "0",
                ]
            ],
        ),
    )
    bars = await adapter.fetch_klines(
        "BTC-USDT",
        "5m",
        datetime(2023, 11, 14, tzinfo=timezone.utc),
        datetime(2023, 11, 15, tzinfo=timezone.utc),
    )
    assert len(bars) == 1
    assert bars[0].symbol == "BTC-USDT"
    assert bars[0].interval == "5m"
    await adapter.aclose()
