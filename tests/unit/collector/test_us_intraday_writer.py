import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.intraday_line_writer import compute_us_point, UsIntradayWriter
from apps.collector.us.trade_hub import TradeAccumulator


def test_compute_us_point_vwap():
    p = compute_us_point(
        "AAPL", datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc),
        price=110.0, cum_amount=3200.0, cum_volume=30)
    assert p.avg_price == pytest.approx(3200.0 / 30)
    assert p.price == 110.0
    assert p.ts.second == 0


def test_compute_us_point_zero_volume_avg_is_price():
    p = compute_us_point(
        "AAPL", datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc),
        price=110.0, cum_amount=0.0, cum_volume=0)
    assert p.avg_price == 110.0


@pytest.mark.asyncio
async def test_flush_writes_and_publishes_in_rth():
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    acc = TradeAccumulator()
    acc.add_trade(price=110.0, size=30, ts=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))
    w = UsIntradayWriter(repo, redis)
    await w.flush("AAPL", acc, now=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))
    assert repo.insert_points.call_count == 1
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["symbol"] == "AAPL" and "avg_price" in payload


@pytest.mark.asyncio
async def test_flush_skips_outside_rth():
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    acc = TradeAccumulator()
    acc.add_trade(price=110.0, size=30, ts=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))
    w = UsIntradayWriter(repo, redis)
    await w.flush("AAPL", acc, now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))  # 08:00 ET 盘前
    assert repo.insert_points.call_count == 0
    assert redis._r.xadd.await_count == 0
