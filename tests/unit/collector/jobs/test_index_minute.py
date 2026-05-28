import pytest
import fakeredis.aioredis
import pandas as pd

from core.cache import keys
from core.cache.redis_client import RedisCache
from apps.collector.jobs.index_minute import refresh_one_index, INDEX_SYMBOLS


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


def test_index_symbols_covers_8_majors():
    expected = {"000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
                "000905.SH", "000852.SH", "000688.SH", "000016.SH"}
    assert set(INDEX_SYMBOLS) == expected


async def test_refresh_one_index_writes_msgpack(cache, monkeypatch):
    fake_df = pd.DataFrame({
        "day": ["2026-05-27 09:30:00", "2026-05-27 09:35:00"],
        "close": [3000.0, 3005.5],
        "volume": [12345, 23456],
    })

    async def fake_ak_call(*args, **kwargs):
        return fake_df

    monkeypatch.setattr("apps.collector.jobs.index_minute.ak_call", fake_ak_call)

    await refresh_one_index("000001.SH", cache=cache)
    payload = await cache.get_msgpack(keys.cache_index_minute("000001.SH", days=1))
    assert payload is not None
    assert payload["symbol"] == "000001.SH"
    assert len(payload["points"]) == 2
    assert "fresh_at" in payload["meta"]
    # 默认调用未传 prev_close, 应为 None
    assert payload["prev_close"] is None


async def test_refresh_one_index_writes_prev_close(cache, monkeypatch):
    """传入 prev_close 应原样写入 payload, 给前端算今日涨跌幅。"""
    fake_df = pd.DataFrame({
        "day": ["2026-05-28 09:35:00"],
        "close": [4099.95],
        "volume": [1000],
    })

    async def fake_ak_call(*args, **kwargs):
        return fake_df

    monkeypatch.setattr("apps.collector.jobs.index_minute.ak_call", fake_ak_call)

    await refresh_one_index("000001.SH", cache=cache, prev_close=4093.7266)
    payload = await cache.get_msgpack(keys.cache_index_minute("000001.SH", days=1))
    assert payload is not None
    assert payload["prev_close"] == pytest.approx(4093.7266)
    # 模拟前端涨跌幅口径
    last = payload["points"][-1]["close"]
    pct = (last - payload["prev_close"]) / payload["prev_close"] * 100
    assert pct == pytest.approx(0.1521, abs=0.01)


async def test_refresh_one_index_handles_ak_failure(cache, monkeypatch):
    async def fake_ak_call(*args, **kwargs):
        raise RuntimeError("sina IndexError")

    monkeypatch.setattr("apps.collector.jobs.index_minute.ak_call", fake_ak_call)

    # 不应抛出 (job 内部 catch 单条失败)
    await refresh_one_index("000001.SH", cache=cache)
    payload = await cache.get_msgpack(keys.cache_index_minute("000001.SH", days=1))
    assert payload is None


async def test_refresh_one_index_writes_market_extras(cache, monkeypatch):
    """传入 market_extras 应原样写入 payload (8 个 A 股指数共享同一份)。"""
    fake_df = pd.DataFrame({
        "day": ["2026-05-28 14:30:00"],
        "close": [4099.95],
        "volume": [1000],
    })

    async def fake_ak_call(*args, **kwargs):
        return fake_df

    monkeypatch.setattr("apps.collector.jobs.index_minute.ak_call", fake_ak_call)

    me = {
        "fund_inflow": 12.3,
        "fund_inflow_label": "北向",
        "amount": 8421.5,
        "amount_unit": "亿元",
        "amount_ratio": 0.052,
    }
    await refresh_one_index("000001.SH", cache=cache,
                            prev_close=4093.7266, market_extras=me)
    payload = await cache.get_msgpack(keys.cache_index_minute("000001.SH", days=1))
    assert payload is not None
    assert payload["market_extras"] == me


def test_ashare_5m_offset_returns_correct_buckets():
    """5m offset 计算: 9:30 -> 0, 14:55 -> 47, 午休/盘前 -> None。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from apps.collector.jobs.index_minute import _ashare_5m_offset
    cn = ZoneInfo("Asia/Shanghai")

    # 9:30 -> offset 0
    assert _ashare_5m_offset(datetime(2026, 5, 28, 9, 30, tzinfo=cn)) == 0
    # 9:34 -> offset 0 (同桶)
    assert _ashare_5m_offset(datetime(2026, 5, 28, 9, 34, tzinfo=cn)) == 0
    # 9:35 -> offset 1
    assert _ashare_5m_offset(datetime(2026, 5, 28, 9, 35, tzinfo=cn)) == 1
    # 11:25 -> offset 23 (上午最后桶)
    assert _ashare_5m_offset(datetime(2026, 5, 28, 11, 25, tzinfo=cn)) == 23
    # 11:30 -> 23 (含边界)
    assert _ashare_5m_offset(datetime(2026, 5, 28, 11, 30, tzinfo=cn)) == 23
    # 12:00 午休 -> None
    assert _ashare_5m_offset(datetime(2026, 5, 28, 12, 0, tzinfo=cn)) is None
    # 13:00 -> offset 24 (下午开盘)
    assert _ashare_5m_offset(datetime(2026, 5, 28, 13, 0, tzinfo=cn)) == 24
    # 14:55 -> offset 47 (收盘最后桶)
    assert _ashare_5m_offset(datetime(2026, 5, 28, 14, 55, tzinfo=cn)) == 47
    # 15:00 -> 47 (含边界)
    assert _ashare_5m_offset(datetime(2026, 5, 28, 15, 0, tzinfo=cn)) == 47
    # 盘前 9:00 -> None
    assert _ashare_5m_offset(datetime(2026, 5, 28, 9, 0, tzinfo=cn)) is None
    # 盘后 15:30 -> None
    assert _ashare_5m_offset(datetime(2026, 5, 28, 15, 30, tzinfo=cn)) is None
