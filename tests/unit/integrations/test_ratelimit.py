import time

import pytest
import fakeredis.aioredis

from core.integrations.ratelimit import RedisTokenBucket


@pytest.fixture
async def redis():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield fake
    await fake.aclose()


async def test_initial_bucket_allows_burst(redis):
    bucket = RedisTokenBucket(redis=redis, key="ratelimit:source:test", rate=5, burst=10)
    for _ in range(10):
        wait_ms = await bucket.acquire(blocking=False)
        assert wait_ms == 0
    wait_ms = await bucket.acquire(blocking=False)
    assert wait_ms > 0


async def test_blocking_acquire_waits_for_token(redis):
    bucket = RedisTokenBucket(redis=redis, key="ratelimit:source:test", rate=10, burst=2)
    # 用尽 burst
    await bucket.acquire(blocking=False)
    await bucket.acquire(blocking=False)
    started = time.monotonic()
    await bucket.acquire(blocking=True)
    waited = time.monotonic() - started
    # rate=10/s 即每 100ms 一个 token,等 ~100ms (容许 sleep 抖动)
    assert 0.05 < waited < 0.5


async def test_acquire_n_tokens(redis):
    bucket = RedisTokenBucket(redis=redis, key="ratelimit:source:test", rate=5, burst=10)
    wait_ms = await bucket.acquire(n=5, blocking=False)
    assert wait_ms == 0
    wait_ms = await bucket.acquire(n=6, blocking=False)
    assert wait_ms > 0
