import pytest
import fakeredis.aioredis

from core.cache.redis_client import RedisCache
from core.integrations.outlets import (
    LocalOutlet, Outcome, OutletLease, OutletPool,
)


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_local_outlet_acquire_returns_empty_env():
    outlet = LocalOutlet()
    lease = await outlet.acquire()
    assert lease.outlet_id == "local"
    assert lease.env == {}


async def test_pool_with_single_outlet_always_returns_it(cache):
    pool = OutletPool([LocalOutlet()], cache=cache)
    for _ in range(5):
        lease = await pool.acquire()
        assert lease.outlet_id == "local"


async def test_pool_skips_banned_outlet_until_cooling_done(cache, monkeypatch):
    import time
    base = time.time()
    monkeypatch.setattr("core.integrations.outlets.pool.time_now", lambda: base)

    o1 = LocalOutlet(name="o1")
    o2 = LocalOutlet(name="o2")
    pool = OutletPool([o1, o2], cache=cache, cooling_seconds=60)
    lease = await pool.acquire()
    await pool.report(lease, Outcome.banned)
    # 接下来不应再选到 lease.outlet_id
    seen = set()
    for _ in range(3):
        l = await pool.acquire()
        seen.add(l.outlet_id)
    assert lease.outlet_id not in seen

    # 超过冷却期后应能再选回来
    monkeypatch.setattr("core.integrations.outlets.pool.time_now", lambda: base + 61)
    seen = set()
    for _ in range(6):
        l = await pool.acquire()
        seen.add(l.outlet_id)
    assert lease.outlet_id in seen


async def test_pool_raises_when_all_banned(cache):
    pool = OutletPool([LocalOutlet(name="o1")], cache=cache, cooling_seconds=60)
    lease = await pool.acquire()
    await pool.report(lease, Outcome.banned)
    with pytest.raises(RuntimeError, match="no usable outlet"):
        await pool.acquire()
