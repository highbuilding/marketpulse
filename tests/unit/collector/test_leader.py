import asyncio

import pytest
import fakeredis.aioredis

from apps.collector.leader import Leader


@pytest.fixture
async def redis():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield fake
    await fake.aclose()


async def test_single_node_always_leader(redis):
    leader = Leader(redis=redis, node_id="node-A", ttl_s=15)
    await leader.try_acquire_once()
    assert leader.is_leader() is True


async def test_two_nodes_only_one_is_leader(redis):
    a = Leader(redis=redis, node_id="node-A", ttl_s=15)
    b = Leader(redis=redis, node_id="node-B", ttl_s=15)
    await a.try_acquire_once()
    await b.try_acquire_once()
    leaders = [a.is_leader(), b.is_leader()]
    assert sum(leaders) == 1


async def test_leader_recovers_after_lock_expires(redis):
    a = Leader(redis=redis, node_id="node-A", ttl_s=15)
    await a.try_acquire_once()
    assert a.is_leader() is True

    # 模拟 a 死掉, 锁过期: 直接删 Redis key
    await redis.delete("state:leader:collector")
    a._is_leader = False  # 模拟 a 知道自己掉锁了

    b = Leader(redis=redis, node_id="node-B", ttl_s=15)
    await b.try_acquire_once()
    assert b.is_leader() is True
