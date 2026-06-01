import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from apps.collector.ashare.intraday_line_writer import IntradayLineWriter, compute_point


def test_compute_point_avg_price():
    p = compute_point("600519.SH", datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc),
                      price=1705.0, cum_amount=1710000.0, cum_volume=1005)
    assert p.avg_price == pytest.approx(1710000.0 / 1005)


def test_compute_point_zero_volume_avg_falls_back_to_price():
    p = compute_point("X", datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc),
                      price=10.0, cum_amount=0.0, cum_volume=0)
    assert p.avg_price == 10.0


def test_compute_point_truncates_to_minute():
    p = compute_point("X", datetime(2026, 6, 1, 1, 31, 45, tzinfo=timezone.utc),
                      price=10.0, cum_amount=100.0, cum_volume=10)
    assert p.ts.second == 0 and p.ts.microsecond == 0


@pytest.mark.asyncio
async def test_write_once_inserts_and_publishes():
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    redis.get_msgpack = AsyncMock(return_value={
        "price": "1705", "volume": 1005, "amount": 1710000.0})
    w = IntradayLineWriter(repo, redis)
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)  # 13:50 BJT 开市
    await w.write_once("600519.SH", now=now)
    assert repo.insert_points.call_count == 1
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["symbol"] == "600519.SH" and "avg_price" in payload


@pytest.mark.asyncio
async def test_write_once_skips_when_no_quote():
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.get_msgpack = AsyncMock(return_value=None)
    w = IntradayLineWriter(repo, redis)
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)
    await w.write_once("600519.SH", now=now)
    assert repo.insert_points.call_count == 0
    assert redis._r.xadd.await_count == 0
