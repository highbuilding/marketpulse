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


async def test_refresh_one_index_handles_ak_failure(cache, monkeypatch):
    async def fake_ak_call(*args, **kwargs):
        raise RuntimeError("sina IndexError")

    monkeypatch.setattr("apps.collector.jobs.index_minute.ak_call", fake_ak_call)

    # 不应抛出 (job 内部 catch 单条失败)
    await refresh_one_index("000001.SH", cache=cache)
    payload = await cache.get_msgpack(keys.cache_index_minute("000001.SH", days=1))
    assert payload is None
