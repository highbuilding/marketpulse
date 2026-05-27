import pytest
import fakeredis.aioredis

from core.cache.redis_client import RedisCache
from core.integrations.breaker import SourceBreaker, BreakerState


@pytest.fixture
async def redis_cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


@pytest.fixture
def breaker(redis_cache):
    return SourceBreaker(
        source="sina",
        cache=redis_cache,
        fail_threshold=0.6,
        min_samples=5,
        window_seconds=60,
        open_duration_seconds=300,
    )


async def test_breaker_starts_closed(breaker):
    assert await breaker.state() == BreakerState.CLOSED
    assert await breaker.allow() is True


async def test_breaker_opens_when_failure_rate_exceeds_threshold(breaker):
    for _ in range(3):
        await breaker.report(success=True)
    for _ in range(7):
        await breaker.report(success=False)
    assert await breaker.state() == BreakerState.OPEN
    assert await breaker.allow() is False


async def test_breaker_does_not_open_with_too_few_samples(breaker):
    for _ in range(4):
        await breaker.report(success=False)
    assert await breaker.state() == BreakerState.CLOSED


async def test_breaker_half_open_after_open_duration(breaker, monkeypatch):
    import time
    base = time.time()
    monkeypatch.setattr("core.integrations.breaker.time_now", lambda: base)
    for _ in range(7):
        await breaker.report(success=False)
    assert await breaker.state() == BreakerState.OPEN
    monkeypatch.setattr("core.integrations.breaker.time_now", lambda: base + 301)
    assert await breaker.state() == BreakerState.HALF_OPEN
    assert await breaker.allow() is True


async def test_breaker_half_open_probe_success_closes(breaker, monkeypatch):
    import time
    base = time.time()
    monkeypatch.setattr("core.integrations.breaker.time_now", lambda: base)
    for _ in range(7):
        await breaker.report(success=False)
    monkeypatch.setattr("core.integrations.breaker.time_now", lambda: base + 301)
    assert await breaker.allow() is True
    await breaker.report(success=True)
    assert await breaker.state() == BreakerState.CLOSED


async def test_breaker_half_open_probe_failure_reopens(breaker, monkeypatch):
    import time
    base = time.time()
    monkeypatch.setattr("core.integrations.breaker.time_now", lambda: base)
    for _ in range(7):
        await breaker.report(success=False)
    monkeypatch.setattr("core.integrations.breaker.time_now", lambda: base + 301)
    await breaker.allow()
    await breaker.report(success=False)
    assert await breaker.state() == BreakerState.OPEN
