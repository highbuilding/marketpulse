# Plan 2 — ak_call 三层中间件 + collector 新增 job

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 collector 进程内为 ak_call 加 Breaker/Ratelimit/Outlet 三层中间件 + Leader 选举, 同时新增 `index_minute` / `market_dashboard` / `refill_consumer` 三个 job + `chip_summary` 改日终预热, 并在 ak_call 出口加 banned 响应识别。完成后: 单 source 失败自动熔断, 出口可切换, 高频任务限速, IP ban 早期信号能识别, 前端读路径所需的预聚合数据已经预先写到 Redis cache。

**Architecture:**
- 三层中间件 `breaker → ratelimit → outlet` 顺序通过, 每层独立可禁用 (env 开关)
- 中间件状态写到 Redis (`state:source:*`, `state:outlet:*`), 单机部署不显眼, 多节点时天然共享
- Leader 锁 (`state:leader:collector`) 单节点永远 acquired, 多节点抢锁
- 新增 job 全部 `ensure_leader()` 守门, 落地后写 Redis cache + 发 bus 事件
- ak_call 仍保留进程级 `_racer_acquire` 全局锁 (子进程隔离已经解决雷区 1, 锁仅作日志/watchdog 入口) — **不删除**, 三层在锁外并存

**Tech Stack:** redis-py 5.x asyncio / pybreaker / Lua (Redis-backed token bucket) / fakeredis (测试) / structlog / APScheduler

**Spec reference:** `docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md` §4.1-4.6, §6.3-6.4

**Plan 1 baseline:** commit `896b9bb`. Plan 2 在此之上展开。

---

## File Structure

### 新增模块

- `core/integrations/breaker.py` — `SourceBreaker` per-source 熔断, 状态 hash 存 Redis
- `core/integrations/ratelimit.py` — `RedisTokenBucket`, 内嵌 Lua 脚本, blocking acquire
- `core/integrations/outlets/__init__.py` — 包入口
- `core/integrations/outlets/base.py` — `Outlet` Protocol + `OutletLease` dataclass + `Outcome` enum
- `core/integrations/outlets/local.py` — `LocalOutlet` (无代理直连, 默认实现)
- `core/integrations/outlets/pool.py` — `OutletPool` 选择 + 状态机 + cooling
- `core/integrations/response_eval.py` — `evaluate_response(df, source) -> Outcome` 检测伪正常 banned 返回
- `apps/collector/leader.py` — `Leader` 抢 Redis SETNX 锁 + 续期 + `is_leader()` 接口

### 新增 job (apps/collector/jobs/)

- `apps/collector/jobs/__init__.py` — 包入口
- `apps/collector/jobs/index_minute.py` — 8 大指数 5min 序列, 每 30s 写 `cache:index:*:minute`
- `apps/collector/jobs/market_dashboard.py` — 大盘聚合包, 每 60s 写 `cache:market:*:dashboard`
- `apps/collector/jobs/refill_consumer.py` — 订阅 `bus:bars.refill_request`, 拉数据回写 cache + 发 `bus:bars.updated`

### 修改模块

- `core/integrations/akshare.py` — `ak_call` 增加三层穿透 + outcome 上报回路
- `core/integrations/akshare_worker.py` — 接受 `env_extras` 注入 (代理出口的 HTTP_PROXY)
- `apps/collector/main.py` — 启动时初始化 Leader + OutletPool + Breaker + Ratelimit, 注入到 ak_call
- `apps/collector/scheduler.py` (新建包装层, 不直接改 core/scheduler) — 在所有 cron 入口加 `ensure_leader()` gate
- `core/scheduler/scheduler.py` — `attach_fundamentals_jobs` 把 `ff:north` 频率 1min → 2min; 加新 job 注册函数
- `core/services/chip_service.py` — 加 `preload_watchlist_chip_summary()` 给日终用 (本 plan 仅添加, 调用方在 Task 11)

### 新增测试

- `tests/unit/integrations/test_breaker.py`
- `tests/unit/integrations/test_ratelimit.py`
- `tests/unit/integrations/test_outlets.py`
- `tests/unit/integrations/test_response_eval.py`
- `tests/unit/collector/test_leader.py`
- `tests/unit/collector/jobs/test_index_minute.py`
- `tests/unit/collector/jobs/test_market_dashboard.py`
- `tests/unit/collector/jobs/test_refill_consumer.py`

### 配置

- `pyproject.toml` — 加 `pybreaker>=1.0`

---

## Task 1: 加 pybreaker 依赖 + Breaker 模块 + 测试

**Files:**
- Modify: `pyproject.toml`
- Create: `core/integrations/breaker.py`
- Create: `tests/unit/integrations/__init__.py` (若不存在)
- Create: `tests/unit/integrations/test_breaker.py`

- [ ] **Step 1: 加 pybreaker 依赖**

修改 `pyproject.toml`, dependencies 数组追加:

```toml
    "pybreaker>=1.0",
```

执行: `. .venv/bin/activate && pip install -e ".[dev]"`
期望: 安装 pybreaker, 无 conflict.

- [ ] **Step 2: 创建测试文件骨架**

`tests/unit/integrations/__init__.py`: 空文件 (`touch tests/unit/integrations/__init__.py`)

- [ ] **Step 3: 写失败测试 (TDD red)**

`tests/unit/integrations/test_breaker.py`:

```python
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
```

- [ ] **Step 4: 跑测试确认 fail**

`. .venv/bin/activate && pytest tests/unit/integrations/test_breaker.py -v`
期望: ImportError on `SourceBreaker`.

- [ ] **Step 5: 实现 core/integrations/breaker.py**

```python
"""Per-source 熔断器, 状态写 Redis hash (跨节点共享决策)。

状态机: closed → open → half_open → closed。
- 滑动窗口失败率 ≥ fail_threshold 且样本 ≥ min_samples → open
- open 持续 open_duration_seconds → half_open (放 1 个探针)
- half_open 探针成功 → closed; 失败 → open

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.3.1
"""
from __future__ import annotations

import time
from enum import Enum

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache

log = structlog.get_logger(__name__)


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def time_now() -> float:
    """间接层用于测试 monkeypatch (time.time 直接 patch 影响 fakeredis 内部)。"""
    return time.time()


class SourceBreaker:
    """每个 source (sina/em/ths) 一个实例。状态共享于 Redis hash。

    Hash fields (state:source:{name}):
      state: closed/open/half_open
      opened_at: float (state=open 时记录)
      window_start: float (滑窗起点)
      success_count, failure_count: int (当前窗口)
    """

    def __init__(
        self,
        *,
        source: str,
        cache: RedisCache,
        fail_threshold: float = 0.6,
        min_samples: int = 5,
        window_seconds: float = 60.0,
        open_duration_seconds: float = 300.0,
    ) -> None:
        self.source = source
        self._cache = cache
        self._key = keys.state_source(source)
        self._fail_threshold = fail_threshold
        self._min_samples = min_samples
        self._window_seconds = window_seconds
        self._open_duration_seconds = open_duration_seconds

    async def state(self) -> BreakerState:
        record = await self._read()
        return self._effective_state(record)

    async def allow(self) -> bool:
        s = await self.state()
        return s in (BreakerState.CLOSED, BreakerState.HALF_OPEN)

    async def report(self, *, success: bool) -> None:
        record = await self._read()
        s = self._effective_state(record)
        now = time_now()
        if s == BreakerState.HALF_OPEN:
            if success:
                await self._write({"state": BreakerState.CLOSED.value,
                                   "window_start": now,
                                   "success_count": 0,
                                   "failure_count": 0,
                                   "opened_at": 0.0})
                log.info("breaker.closed", source=self.source)
            else:
                await self._write({"state": BreakerState.OPEN.value,
                                   "opened_at": now,
                                   "window_start": now,
                                   "success_count": 0,
                                   "failure_count": 0})
                log.warning("breaker.opened", source=self.source, reason="half_open_probe_failed")
            return
        # closed: 累计在滑动窗口内
        window_start = float(record.get("window_start", now))
        if now - window_start > self._window_seconds:
            window_start = now
            success_count = 0
            failure_count = 0
        else:
            success_count = int(record.get("success_count", 0))
            failure_count = int(record.get("failure_count", 0))
        if success:
            success_count += 1
        else:
            failure_count += 1
        total = success_count + failure_count
        rate = failure_count / total if total else 0.0
        if total >= self._min_samples and rate >= self._fail_threshold:
            await self._write({"state": BreakerState.OPEN.value,
                               "opened_at": now,
                               "window_start": window_start,
                               "success_count": success_count,
                               "failure_count": failure_count})
            log.warning("breaker.opened", source=self.source,
                        rate=round(rate, 3), samples=total)
        else:
            await self._write({"state": BreakerState.CLOSED.value,
                               "window_start": window_start,
                               "success_count": success_count,
                               "failure_count": failure_count,
                               "opened_at": float(record.get("opened_at", 0.0))})

    def _effective_state(self, record: dict) -> BreakerState:
        s = record.get("state", BreakerState.CLOSED.value)
        if s == BreakerState.OPEN.value:
            opened_at = float(record.get("opened_at", 0.0))
            if time_now() - opened_at >= self._open_duration_seconds:
                return BreakerState.HALF_OPEN
            return BreakerState.OPEN
        return BreakerState.CLOSED if s == BreakerState.CLOSED.value else BreakerState.HALF_OPEN

    async def _read(self) -> dict:
        keys.validate(self._key)
        raw = await self._cache._r.hgetall(self._key)
        if not raw:
            return {}
        out = {}
        for k, v in raw.items():
            kk = k.decode() if isinstance(k, bytes) else k
            vv = v.decode() if isinstance(v, bytes) else v
            out[kk] = vv
        return out

    async def _write(self, fields: dict) -> None:
        keys.validate(self._key)
        await self._cache._r.hset(self._key, mapping={k: str(v) for k, v in fields.items()})
```

- [ ] **Step 6: 跑测试 PASS**

`. .venv/bin/activate && pytest tests/unit/integrations/test_breaker.py -v`
期望: 6 个测试全 pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml core/integrations/breaker.py tests/unit/integrations/__init__.py tests/unit/integrations/test_breaker.py
git commit -m "feat(integrations): SourceBreaker per-source 熔断 + Redis 状态 + 单测"
```

---

## Task 2: Ratelimit (纯 Lua + Redis 令牌桶) + 测试

**Files:**
- Create: `core/integrations/ratelimit.py`
- Create: `tests/unit/integrations/test_ratelimit.py`

- [ ] **Step 1: 失败测试**

`tests/unit/integrations/test_ratelimit.py`:

```python
import asyncio
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
    # rate=10/s 即每 100ms 一个 token,等 ~100ms
    assert 0.05 < waited < 0.5


async def test_acquire_n_tokens(redis):
    bucket = RedisTokenBucket(redis=redis, key="ratelimit:source:test", rate=5, burst=10)
    wait_ms = await bucket.acquire(n=5, blocking=False)
    assert wait_ms == 0
    wait_ms = await bucket.acquire(n=6, blocking=False)
    assert wait_ms > 0
```

- [ ] **Step 2: 跑确认 fail**

`. .venv/bin/activate && pytest tests/unit/integrations/test_ratelimit.py -v`
期望: ImportError on `RedisTokenBucket`.

- [ ] **Step 3: 实现 core/integrations/ratelimit.py**

```python
"""Redis-backed 令牌桶 (纯 Lua, 原子)。

设计:
- 一次 EVAL 完成 "看桶有没有 token / 没有就告诉我多久能续上"
- 状态: hash field {tokens: float, last_refill: float}
- 阻塞模式: 拿不到 token 就 sleep wait_ms 后重试 (loop 至多 N 次)

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.3.2
"""
from __future__ import annotations

import asyncio
import time

import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)


# Lua 脚本: 原子取 N 个 token
# KEYS[1] = bucket key
# ARGV[1] = rate (tokens/sec)
# ARGV[2] = burst (max bucket capacity)
# ARGV[3] = n  (request token count)
# ARGV[4] = now (unix seconds, float)
#
# 返回:
#   {1, 0}  = 拿到, 等待毫秒 = 0
#   {0, ms} = 没拿到, 还需等 ms 毫秒
_LUA_ACQUIRE = """
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local n = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'last')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then
  tokens = burst
  last = now
end

local elapsed = math.max(0, now - last)
tokens = math.min(burst, tokens + elapsed * rate)

if tokens >= n then
  tokens = tokens - n
  redis.call('HMSET', KEYS[1], 'tokens', tokens, 'last', now)
  redis.call('EXPIRE', KEYS[1], 3600)
  return {1, 0}
else
  redis.call('HMSET', KEYS[1], 'tokens', tokens, 'last', now)
  redis.call('EXPIRE', KEYS[1], 3600)
  local needed = n - tokens
  local wait_ms = math.ceil(needed / rate * 1000)
  return {0, wait_ms}
end
"""


class RedisTokenBucket:
    def __init__(
        self,
        *,
        redis: AsyncRedis,
        key: str,
        rate: float,
        burst: int,
    ) -> None:
        keys.validate(key)
        self._r = redis
        self._key = key
        self._rate = rate
        self._burst = burst
        self._script = redis.register_script(_LUA_ACQUIRE)

    async def acquire(self, n: int = 1, *, blocking: bool = True, max_wait_s: float = 30.0) -> int:
        """成功返回 0, blocking=False 且不够时返回需要等待的毫秒数。

        blocking=True 模式下持续等待至最多 max_wait_s 秒, 超时 raise TimeoutError。
        """
        deadline = time.monotonic() + max_wait_s if blocking else None
        while True:
            now = time.time()
            ok, wait_ms = await self._script(keys=[self._key],
                                              args=[self._rate, self._burst, n, now])
            if int(ok) == 1:
                return 0
            wait_ms = int(wait_ms)
            if not blocking:
                return wait_ms
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"ratelimit timeout key={self._key} waited>{max_wait_s}s")
            await asyncio.sleep(min(wait_ms / 1000.0, 1.0))
```

- [ ] **Step 4: 跑测试 PASS**

`. .venv/bin/activate && pytest tests/unit/integrations/test_ratelimit.py -v`
期望: 3 个 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/integrations/ratelimit.py tests/unit/integrations/test_ratelimit.py
git commit -m "feat(integrations): Redis Lua 令牌桶限速器 + 单测"
```

---

## Task 3: Outlet Protocol + LocalOutlet + OutletPool + 测试

**Files:**
- Create: `core/integrations/outlets/__init__.py`
- Create: `core/integrations/outlets/base.py`
- Create: `core/integrations/outlets/local.py`
- Create: `core/integrations/outlets/pool.py`
- Create: `tests/unit/integrations/test_outlets.py`

- [ ] **Step 1: 失败测试**

`tests/unit/integrations/test_outlets.py`:

```python
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
```

- [ ] **Step 2: 跑确认 fail**

`. .venv/bin/activate && pytest tests/unit/integrations/test_outlets.py -v`
期望: ImportError.

- [ ] **Step 3: base.py**

```python
# core/integrations/outlets/base.py
"""Outlet 抽象 — 出口管理 (单 IP / 多代理 / VPN 池)。

LocalOutlet 是默认实现 (无代理直连)。
未来商业代理池作为 Outlet 子类加入, ak_call 业务代码无感知。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Outcome(str, Enum):
    ok = "ok"
    empty = "empty"
    parse_error = "parse_error"
    timeout = "timeout"
    banned = "banned"


@dataclass(frozen=True)
class OutletLease:
    outlet_id: str
    env: dict[str, str] = field(default_factory=dict)


class Outlet(Protocol):
    name: str

    async def acquire(self) -> OutletLease: ...
    async def report(self, lease: OutletLease, outcome: Outcome) -> None: ...
```

- [ ] **Step 4: local.py**

```python
# core/integrations/outlets/local.py
"""LocalOutlet — 不走代理, 直连本机网络出口。

acquire() 总是成功, env 为空 (子进程不注入 HTTP_PROXY)。
report() noop — 单一本地出口没什么状态可记录。
"""
from __future__ import annotations

from core.integrations.outlets.base import Outcome, Outlet, OutletLease


class LocalOutlet:
    def __init__(self, name: str = "local") -> None:
        self.name = name

    async def acquire(self) -> OutletLease:
        return OutletLease(outlet_id=self.name, env={})

    async def report(self, lease: OutletLease, outcome: Outcome) -> None:
        # 本地出口不分别管理状态, 由 SourceBreaker 在更高层兜
        return
```

- [ ] **Step 5: pool.py**

```python
# core/integrations/outlets/pool.py
"""OutletPool — 在多个 Outlet 间路由, banned 自动 cooling N 分钟。

状态记录在 Redis (state:outlet:{id}), 跨节点共享决策。

参考: §4.3.3
"""
from __future__ import annotations

import time
from typing import Sequence

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.integrations.outlets.base import Outcome, Outlet, OutletLease

log = structlog.get_logger(__name__)


def time_now() -> float:
    return time.time()


class OutletPool:
    def __init__(
        self,
        outlets: Sequence[Outlet],
        *,
        cache: RedisCache,
        cooling_seconds: float = 1800.0,
    ) -> None:
        if not outlets:
            raise ValueError("OutletPool requires at least 1 Outlet")
        self._outlets = list(outlets)
        self._cache = cache
        self._cooling_seconds = cooling_seconds
        self._next_idx = 0  # round-robin

    async def acquire(self) -> OutletLease:
        """轮询挑一个未 cooling 的 outlet。全 cooling raise RuntimeError。"""
        n = len(self._outlets)
        for _ in range(n):
            idx = self._next_idx % n
            self._next_idx += 1
            outlet = self._outlets[idx]
            if not await self._is_cooling(outlet.name):
                lease = await outlet.acquire()
                return lease
        raise RuntimeError("no usable outlet (all banned/cooling)")

    async def report(self, lease: OutletLease, outcome: Outcome) -> None:
        outlet = self._find(lease.outlet_id)
        if outlet is not None:
            await outlet.report(lease, outcome)
        if outcome == Outcome.banned:
            await self._mark_cooling(lease.outlet_id)
            log.warning("outlet.banned", outlet=lease.outlet_id,
                        cooling_seconds=self._cooling_seconds)

    def _find(self, outlet_id: str) -> Outlet | None:
        for o in self._outlets:
            if o.name == outlet_id:
                return o
        return None

    async def _is_cooling(self, outlet_id: str) -> bool:
        key = keys.state_outlet(outlet_id)
        keys.validate(key)
        raw = await self._cache._r.hget(key, "banned_until")
        if raw is None:
            return False
        try:
            until = float(raw.decode() if isinstance(raw, bytes) else raw)
        except (TypeError, ValueError):
            return False
        return time_now() < until

    async def _mark_cooling(self, outlet_id: str) -> None:
        key = keys.state_outlet(outlet_id)
        keys.validate(key)
        until = time_now() + self._cooling_seconds
        await self._cache._r.hset(key, mapping={"banned_until": str(until)})
```

- [ ] **Step 6: outlets/__init__.py**

```python
from core.integrations.outlets.base import Outcome, Outlet, OutletLease
from core.integrations.outlets.local import LocalOutlet
from core.integrations.outlets.pool import OutletPool

__all__ = ["Outcome", "Outlet", "OutletLease", "LocalOutlet", "OutletPool"]
```

- [ ] **Step 7: 跑测试 PASS**

`. .venv/bin/activate && pytest tests/unit/integrations/test_outlets.py -v`
期望: 4 个 PASS.

- [ ] **Step 8: Commit**

```bash
git add core/integrations/outlets/ tests/unit/integrations/test_outlets.py
git commit -m "feat(integrations): Outlet 抽象 + LocalOutlet + OutletPool(Redis cooling)"
```

---

## Task 4: response evaluator (banned 检测) + 测试

**Files:**
- Create: `core/integrations/response_eval.py`
- Create: `tests/unit/integrations/test_response_eval.py`

- [ ] **Step 1: 失败测试**

`tests/unit/integrations/test_response_eval.py`:

```python
import pandas as pd
import pytest

from core.integrations.outlets import Outcome
from core.integrations.response_eval import evaluate_response


def test_evaluate_none_is_empty():
    assert evaluate_response(None, source="sina") == Outcome.empty


def test_evaluate_empty_dataframe_is_empty():
    assert evaluate_response(pd.DataFrame(), source="sina") == Outcome.empty


def test_evaluate_normal_sina_quote_df_is_ok():
    df = pd.DataFrame({"day": ["2026-05-27 09:30:00"], "close": [3000.5], "volume": [12345]})
    assert evaluate_response(df, source="sina") == Outcome.ok


def test_evaluate_sina_html_response_is_banned():
    # sina 反爬时偶尔返回 1 行单列 HTML 片段
    df = pd.DataFrame({"col0": ["<html><body>access denied</body></html>"]})
    assert evaluate_response(df, source="sina") == Outcome.banned


def test_evaluate_em_returns_ok_for_normal_dataframe():
    df = pd.DataFrame({"代码": ["600519"], "最新价": [1800.0], "涨跌幅": [1.5]})
    assert evaluate_response(df, source="em") == Outcome.ok


def test_evaluate_unknown_source_falls_back_to_basic_check():
    df = pd.DataFrame({"x": [1, 2, 3]})
    assert evaluate_response(df, source="unknown") == Outcome.ok
```

- [ ] **Step 2: 跑确认 fail**

`. .venv/bin/activate && pytest tests/unit/integrations/test_response_eval.py -v`
期望: ImportError.

- [ ] **Step 3: 实现 core/integrations/response_eval.py**

```python
"""响应质量评估 — 区分 ok / empty / banned 等结果, 给 breaker / outlet 上报。

关键洞察: "成功返回但内容异常" 也是失败 (sina 反爬时返回伪正常 HTML)。
参考: §4.3.4
"""
from __future__ import annotations

import re
from typing import Any

from core.integrations.outlets import Outcome

_HTML_RE = re.compile(r"<\s*(html|body|head|script|head)\b", re.IGNORECASE)


def evaluate_response(result: Any, *, source: str) -> Outcome:
    """识别 banned 伪正常返回。

    规则:
    - None / 空 → empty
    - sina 系: 列只有 1 个 + 任一格内容看起来是 HTML → banned
    - 其他源: 非空 → ok (源特定规则可后续扩展)
    """
    if result is None:
        return Outcome.empty
    shape = getattr(result, "shape", None)
    empty = getattr(result, "empty", None)
    if empty is True:
        return Outcome.empty
    if shape is not None:
        try:
            rows, *_ = tuple(shape)
            if rows == 0:
                return Outcome.empty
        except (TypeError, ValueError):
            pass
    if source == "sina":
        try:
            cols = list(getattr(result, "columns", []))
            if len(cols) <= 1:
                # 取第一列前 1 行内容判断是否 HTML
                first_col = cols[0] if cols else None
                if first_col is not None:
                    sample = str(result[first_col].iloc[0])
                    if _HTML_RE.search(sample):
                        return Outcome.banned
        except Exception:  # noqa: BLE001
            pass
    return Outcome.ok
```

- [ ] **Step 4: 跑测试 PASS**

期望: 6 个 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/integrations/response_eval.py tests/unit/integrations/test_response_eval.py
git commit -m "feat(integrations): response_eval — 识别 sina banned 伪正常返回"
```

---

## Task 5: Leader 抢锁 + 续期 + 测试

**Files:**
- Create: `apps/collector/leader.py`
- Create: `tests/unit/collector/__init__.py` (若不存在)
- Create: `tests/unit/collector/test_leader.py`

- [ ] **Step 1: 准备测试目录**

`touch tests/unit/collector/__init__.py`

- [ ] **Step 2: 失败测试 — tests/unit/collector/test_leader.py**

```python
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
```

- [ ] **Step 3: 跑确认 fail**

`. .venv/bin/activate && pytest tests/unit/collector/test_leader.py -v`
期望: ImportError.

- [ ] **Step 4: 实现 apps/collector/leader.py**

```python
"""Collector Leader 选举 — 单 Redis SETNX 锁 + 续期。

设计: 单节点部署时永远续期成功; 多节点时抢锁, 只 leader 跑 cron job。
RTO ≤ ttl_s (默认 15s)。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.2
"""
from __future__ import annotations

import asyncio
import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)


class Leader:
    def __init__(
        self,
        *,
        redis: AsyncRedis,
        node_id: str,
        ttl_s: int = 15,
        renew_interval_s: int = 5,
    ) -> None:
        self._r = redis
        self._node_id = node_id
        self._ttl_s = ttl_s
        self._renew_interval_s = renew_interval_s
        self._key = keys.state_leader_collector()
        self._is_leader = False
        self._stopped = False

    def is_leader(self) -> bool:
        return self._is_leader

    async def try_acquire_once(self) -> bool:
        """一轮抢锁/续期。供测试 / 启动时 warm-up 用。"""
        keys.validate(self._key)
        # 用 SET NX EX 抢: 没人持锁就成为 leader
        ok = await self._r.set(self._key, self._node_id, nx=True, ex=self._ttl_s)
        if ok:
            self._is_leader = True
            log.info("leader.acquired", node=self._node_id)
            return True
        # 已有人持锁: 看是不是自己
        current = await self._r.get(self._key)
        if current is not None:
            current_id = current.decode() if isinstance(current, bytes) else current
            if current_id == self._node_id:
                await self._r.expire(self._key, self._ttl_s)
                self._is_leader = True
                return True
        if self._is_leader:
            log.warning("leader.lost", node=self._node_id, current=current)
        self._is_leader = False
        return False

    async def acquire_loop(self) -> None:
        """长循环: 每 renew_interval_s 抢一次锁/续期。后台 task 跑。"""
        log.info("leader.loop_start", node=self._node_id,
                 ttl_s=self._ttl_s, renew=self._renew_interval_s)
        while not self._stopped:
            try:
                await self.try_acquire_once()
            except Exception as e:  # noqa: BLE001
                log.warning("leader.renew_failed", node=self._node_id, error=str(e))
            await asyncio.sleep(self._renew_interval_s)
        log.info("leader.loop_stopped", node=self._node_id)

    async def release(self) -> None:
        """主动放锁 (shutdown 时调用)。只放属于自己的锁。"""
        self._stopped = True
        current = await self._r.get(self._key)
        if current is None:
            return
        current_id = current.decode() if isinstance(current, bytes) else current
        if current_id == self._node_id:
            await self._r.delete(self._key)
            log.info("leader.released", node=self._node_id)
        self._is_leader = False
```

- [ ] **Step 5: 跑测试 PASS**

期望: 3 个 PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/collector/leader.py tests/unit/collector/__init__.py tests/unit/collector/test_leader.py
git commit -m "feat(collector): Leader 抢 Redis SETNX 锁 + 续期 + 单测"
```

---

## Task 6: ak_call 接入三层中间件 (Outlet + Breaker + Ratelimit)

**Files:**
- Modify: `core/integrations/akshare.py`
- Modify: `core/integrations/akshare_worker.py`
- Create: `core/integrations/ak_middleware.py` (注入容器, 给 ak_call 用)

这一步**最敏感**, 改动 ak_call 主路径。原有的 `_racer_acquire` 全局锁**保留** (子进程已绕过 V8 race, 但锁仍作日志和 watchdog 入口)。三层并行加在锁外。

- [ ] **Step 1: 新建 ak_middleware.py — 注入容器**

```python
"""ak_call 的依赖注入容器。

collector 启动时 setup() 注入 OutletPool / Breaker map / Ratelimit map,
ak_call 调用时从容器拿。

api 进程不调 ak_call, 所以注入是 None 也能跑 — 但 api 进程不应该走到 ak_call,
任何调用都说明 read 路径未切换 (Plan 3 才解决)。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §4.3
"""
from __future__ import annotations

from dataclasses import dataclass

from core.integrations.breaker import SourceBreaker
from core.integrations.outlets import OutletPool
from core.integrations.ratelimit import RedisTokenBucket


@dataclass
class AkMiddleware:
    outlet_pool: OutletPool
    breakers: dict[str, SourceBreaker]   # source -> SourceBreaker
    ratelimits: dict[str, RedisTokenBucket]   # source -> RedisTokenBucket


_container: AkMiddleware | None = None


def setup(middleware: AkMiddleware) -> None:
    global _container
    _container = middleware


def get() -> AkMiddleware | None:
    return _container


def reset() -> None:
    """测试用 — 清空容器以隔离测试。"""
    global _container
    _container = None
```

- [ ] **Step 2: 改 ak_call_in 让 worker 接 env_extras**

修改 `core/integrations/akshare_worker.py`, 在 main() 开头加: 从 env 取代理 (实际上 worker 已经在 env 中跑, 不需改 worker 本身; ak_call 父进程负责构造 env 后传给 subprocess)。

确认 worker 已经有 `os.environ.setdefault("NO_PROXY", "*")` (它有)。改 worker 接受 env 注入的方式:**改 ak_call 父侧而不是 worker 侧**, 父侧用 `subprocess.run(..., env={...})` 时 worker 启动时通过 `os.environ` 自然拿到。Worker 现有逻辑保留。

**结论**: worker 不动, 修改集中在 ak_call 父侧的 `_run_ak_in_child_process`, 让它接受 `env_extras` 参数。

- [ ] **Step 3: 改 core/integrations/akshare.py**

替换 ak_call 实现为三层穿透版本。**关键**:三层中间件默认放行 (`AkMiddleware` 容器没 setup 时跳过), 这样 api 进程或测试期间不需要 Redis。

新版 ak_call 整体逻辑:

```python
from __future__ import annotations

import asyncio
import os
import pickle
import subprocess
import sys
import time
import tempfile
from typing import Any

import structlog

from core.integrations import ak_middleware
from core.integrations.outlets import Outcome
from core.integrations.response_eval import evaluate_response
from core.services._locks import acquire as _racer_acquire

log = structlog.get_logger(__name__)
_DEFAULT_TIMEOUT_S = float(os.getenv("AK_CALL_TIMEOUT_S", "25"))

# func_name -> source 映射(用于 breaker/ratelimit 分发)
# 不完整时默认 source="sina"(akshare 大多走 sina 系)
_FUNC_TO_SOURCE = {
    # em 系
    "stock_zh_a_spot_em": "em",
    "stock_individual_fund_flow": "em",
    "stock_hsgt_hist_em": "em",
    "stock_board_industry_name_em": "em",
    "stock_board_concept_name_em": "em",
    "stock_hk_index_daily_em": "em",
    # ths 系
    "stock_board_industry_cons_ths": "ths",
    "stock_board_concept_cons_ths": "ths",
    # 其他 → sina
}


def _infer_source(func_name: str) -> str:
    return _FUNC_TO_SOURCE.get(func_name, "sina")


async def ak_call(
    func_name: str,
    *args: Any,
    caller: str | None = None,
    ak_timeout_s: float | None = None,
    **kwargs: Any,
) -> Any:
    label = caller or func_name
    source = _infer_source(func_name)
    middleware = ak_middleware.get()

    # 一层: breaker check
    if middleware is not None and source in middleware.breakers:
        breaker = middleware.breakers[source]
        if not await breaker.allow():
            log.warning("ak_call.breaker_open", func=func_name, caller=label, source=source)
            raise RuntimeError(f"breaker open for source={source}")

    # 二层: ratelimit acquire (blocking)
    if middleware is not None and source in middleware.ratelimits:
        await middleware.ratelimits[source].acquire(blocking=True)

    # 三层: outlet acquire
    lease = None
    env_extras: dict[str, str] = {}
    if middleware is not None:
        lease = await middleware.outlet_pool.acquire()
        env_extras = dict(lease.env)

    async with _racer_acquire(f"ak:{label}"):
        started = time.monotonic()
        timeout_s = ak_timeout_s or _DEFAULT_TIMEOUT_S
        log.info("ak_call.start", func=func_name, caller=label, source=source,
                 outlet=lease.outlet_id if lease else None,
                 timeout_s=timeout_s, args_count=len(args),
                 kwargs=_safe_kwargs(kwargs))
        outcome: Outcome
        result: Any = None
        try:
            result = await asyncio.to_thread(
                _run_ak_in_child_process,
                func_name, args, kwargs, timeout_s, env_extras,
            )
            outcome = evaluate_response(result, source=source)
        except subprocess.TimeoutExpired:
            outcome = Outcome.timeout
            await _report_all(middleware, source, lease, outcome)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            log.warning("ak_call.timeout", func=func_name, caller=label,
                        source=source, elapsed_ms=elapsed_ms)
            raise
        except Exception as e:
            outcome = Outcome.parse_error
            await _report_all(middleware, source, lease, outcome)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            log.warning("ak_call.failed", func=func_name, caller=label,
                        source=source, elapsed_ms=elapsed_ms,
                        error_type=type(e).__name__, error=str(e))
            raise

        await _report_all(middleware, source, lease, outcome)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if outcome == Outcome.banned:
            log.warning("ak_call.banned_signature", func=func_name, caller=label,
                        source=source, elapsed_ms=elapsed_ms)
            raise RuntimeError(f"banned signature detected for source={source}")
        log.info("ak_call.success", func=func_name, caller=label, source=source,
                 outcome=outcome.value, elapsed_ms=elapsed_ms,
                 result=_result_summary(result))
        return result


async def _report_all(middleware, source, lease, outcome) -> None:
    if middleware is None:
        return
    success = outcome == Outcome.ok
    if source in middleware.breakers:
        try:
            await middleware.breakers[source].report(success=success)
        except Exception as e:  # noqa: BLE001
            log.warning("breaker.report_failed", source=source, error=str(e))
    if lease is not None:
        try:
            await middleware.outlet_pool.report(lease, outcome)
        except Exception as e:  # noqa: BLE001
            log.warning("outlet.report_failed", outlet=lease.outlet_id, error=str(e))


def _safe_kwargs(kwargs):
    out = {}
    for key, value in kwargs.items():
        text = str(value)
        out[key] = text if len(text) <= 80 else f"{text[:77]}..."
    return out


def _result_summary(result):
    shape = getattr(result, "shape", None)
    if shape is not None:
        try:
            return {"type": type(result).__name__, "shape": tuple(shape)}
        except TypeError:
            return {"type": type(result).__name__}
    if isinstance(result, (list, tuple, set, dict)):
        return {"type": type(result).__name__, "len": len(result)}
    return {"type": type(result).__name__}


def _run_ak_in_child_process(func_name, args, kwargs, timeout_s, env_extras):
    with tempfile.TemporaryDirectory(prefix="marketpulse-ak-") as tmp:
        input_path = os.path.join(tmp, "input.pkl")
        output_path = os.path.join(tmp, "output.pkl")
        with open(input_path, "wb") as fp:
            pickle.dump((args, kwargs), fp)
        env = {**os.environ, **env_extras}
        proc = subprocess.run(
            [
                sys.executable, "-m", "core.integrations.akshare_worker",
                func_name, input_path, output_path,
            ],
            cwd=os.getcwd(),
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")[-4000:]
            stdout = proc.stdout.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(
                f"akshare worker failed rc={proc.returncode}: {func_name}; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        if not os.path.exists(output_path):
            raise RuntimeError(f"akshare worker produced no output: {func_name}")
        with open(output_path, "rb") as fp:
            status, payload = pickle.load(fp)
        if status == "ok":
            return payload
        error_type, message, tb = payload
        raise RuntimeError(f"{func_name} failed in child process: {error_type}: {message}\n{tb}")
```

- [ ] **Step 4: import smoke**

`. .venv/bin/activate && python -c "from core.integrations.akshare import ak_call; print('OK')"`
期望: `OK`

- [ ] **Step 5: 跑现有所有非集成测试 — 验证 ak_call 没 break**

`. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -5`
期望: 271+ 全 pass, **没有 regression** (中间件没注入时 ak_call 行为等价旧版).

- [ ] **Step 6: Commit**

```bash
git add core/integrations/akshare.py core/integrations/ak_middleware.py
git commit -m "feat(integrations): ak_call 加三层中间件穿透(breaker/ratelimit/outlet, 默认放行)"
```

---

## Task 7: collector 启动期初始化中间件 + Leader

**Files:**
- Modify: `apps/collector/main.py`

- [ ] **Step 1: 在 lifespan 中初始化中间件 + Leader**

修改 `apps/collector/main.py`, 在 lifespan 内、scheduler 启动**之前**, 加初始化:

```python
# (在 redis_ok = await get_redis_cache().ping() 之后, scheduler 启动之前插入)

# Plan 2: 初始化 ak_call 三层中间件 + Leader
from apps.api.deps import get_redis_cache as _get_redis_cache
from core.cache.redis_client import make_redis
from core.integrations import ak_middleware
from core.integrations.breaker import SourceBreaker
from core.integrations.outlets import LocalOutlet, OutletPool
from core.integrations.ratelimit import RedisTokenBucket
from apps.collector.leader import Leader

_redis_for_mw = make_redis(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
_redis_cache = _get_redis_cache()

# Outlet: 单一 LocalOutlet (无代理)
_outlet_pool = OutletPool([LocalOutlet()], cache=_redis_cache, cooling_seconds=1800)

# Breakers: per-source
_breakers = {
    "sina": SourceBreaker(source="sina", cache=_redis_cache),
    "em": SourceBreaker(source="em", cache=_redis_cache),
    "ths": SourceBreaker(source="ths", cache=_redis_cache),
}

# Ratelimits: per-source 令牌桶 (rate=tok/s, burst=最大瞬时)
_ratelimits = {
    "sina": RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:sina", rate=5, burst=20),
    "em":   RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:em",   rate=10, burst=50),
    "ths":  RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:ths",  rate=3, burst=10),
}

ak_middleware.setup(ak_middleware.AkMiddleware(
    outlet_pool=_outlet_pool, breakers=_breakers, ratelimits=_ratelimits,
))
log.info("ak_middleware.ready",
         outlets=["local"], breakers=list(_breakers.keys()),
         ratelimits=list(_ratelimits.keys()))

# Leader
import socket
_node_id = f"{socket.gethostname()}-{os.getpid()}"
leader = Leader(redis=_redis_for_mw, node_id=_node_id, ttl_s=15, renew_interval_s=5)
await leader.try_acquire_once()
_leader_task = asyncio.create_task(leader.acquire_loop())
log.info("leader.bootstrapped", node=_node_id, is_leader=leader.is_leader())

# (然后 build_scheduler / attach_* / sched.start() 照旧)
```

并在 `finally:` 块中加:
```python
        leader._stopped = True
        await leader.release()
```

(放在 sched.shutdown 之前)

- [ ] **Step 2: import smoke**

`. .venv/bin/activate && python -c "from apps.collector.main import app; print('OK')"`
期望: `OK`

- [ ] **Step 3: 起 collector 验证**

```bash
pkill -9 -f "apps.collector.main" 2>/dev/null; sleep 2
> /tmp/collector.log
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
sleep 10
grep -E "ak_middleware.ready|leader.acquired|scheduler.built|collector.started" /tmp/collector.log
```
期望: 4 个 event 都有, leader.acquired with node=hostname-pid

- [ ] **Step 4: 验证 leader 写入 Redis**

```bash
docker exec marketpulse-redis-dev redis-cli get "state:leader:collector"
```
期望: 返回 node-id 字符串.

- [ ] **Step 5: Commit**

```bash
git add apps/collector/main.py
git commit -m "feat(collector): 启动期初始化 ak 中间件 + Leader 抢锁"
```

---

## Task 8: collector job — index_minute (8 大指数 5m 序列)

**Files:**
- Create: `apps/collector/jobs/__init__.py`
- Create: `apps/collector/jobs/index_minute.py`
- Create: `tests/unit/collector/jobs/__init__.py`
- Create: `tests/unit/collector/jobs/test_index_minute.py`
- Modify: `core/scheduler/scheduler.py` 加 `attach_index_minute_job(sched, ...)`
- Modify: `apps/collector/main.py` 注册新 job

- [ ] **Step 1: 创建 package init 文件**

```bash
touch apps/collector/jobs/__init__.py
touch tests/unit/collector/jobs/__init__.py
```

- [ ] **Step 2: 失败测试 — test_index_minute.py**

```python
import pytest
import fakeredis.aioredis
import pandas as pd
from unittest.mock import AsyncMock

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
    # cache 中没该 key (拉失败不写)
    payload = await cache.get_msgpack(keys.cache_index_minute("000001.SH", days=1))
    assert payload is None
```

- [ ] **Step 3: 跑确认 fail**

`. .venv/bin/activate && pytest tests/unit/collector/jobs/test_index_minute.py -v`
期望: ImportError.

- [ ] **Step 4: 实现 apps/collector/jobs/index_minute.py**

```python
"""8 大 A 股指数 5min 序列预拉取 — Plan 2 Stage 4 引入。

替代 apps/api/routes/indices.py 的"路由内 ak_call",前端读 cache 不打 ak。
交易时段每 30s 一次, 非交易时段每 5min 一次 (cron 设置在 attach 函数里)。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §3.2
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.integrations.akshare import ak_call

log = structlog.get_logger(__name__)

INDEX_SYMBOLS = [
    "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
    "000905.SH", "000852.SH", "000688.SH", "000016.SH",
]

_CN_TZ = ZoneInfo("Asia/Shanghai")
_CACHE_TTL_S = 90  # 30s 写 + 90s TTL = 充足覆盖


def _to_sina_a(symbol: str) -> str:
    code, mkt = symbol.split(".")
    return f"{mkt.lower()}{code}"


async def refresh_one_index(symbol: str, *, cache: RedisCache) -> None:
    """拉一个指数当日 5m 数据, 写 cache。单条失败仅 warning, 不抛。"""
    sina_code = _to_sina_a(symbol)
    try:
        df = await ak_call(
            "stock_zh_a_minute", symbol=sina_code, period="5", adjust="",
            caller=f"index_minute.refresh:{symbol}",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("index_minute.fetch_failed", symbol=symbol, error=str(e))
        return

    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=2)
    points = []
    for _, row in df.iterrows():
        try:
            naive = datetime.fromisoformat(str(row["day"]).replace(" ", "T"))
            ts = naive.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
            if ts < cutoff_utc:
                continue
            points.append({
                "ts": ts.isoformat(),
                "close": float(row["close"]),
                "volume": int(float(row["volume"])),
            })
        except Exception:  # noqa: BLE001
            continue
    # 取最近一个交易日 (按北京日期)
    if points:
        last_date_cn = datetime.fromisoformat(points[-1]["ts"]).astimezone(_CN_TZ).date()
        points = [p for p in points
                  if datetime.fromisoformat(p["ts"]).astimezone(_CN_TZ).date() == last_date_cn]
    payload = {
        "symbol": symbol,
        "granularity": "5m",
        "points": points,
        "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(),
                 "stale": False, "source": "sina"},
    }
    await cache.set_msgpack(keys.cache_index_minute(symbol, days=1), payload, ttl_s=_CACHE_TTL_S)
    log.info("index_minute.cached", symbol=symbol, points=len(points))


async def refresh_all_indices(cache: RedisCache) -> None:
    """循环刷新 8 个指数。单条失败不影响后续。"""
    for symbol in INDEX_SYMBOLS:
        await refresh_one_index(symbol, cache=cache)
```

- [ ] **Step 5: 跑测试 PASS**

期望: 3 个 PASS.

- [ ] **Step 6: 改 core/scheduler/scheduler.py 加 attach 函数**

在 `scheduler.py` 末尾追加:

```python
def attach_index_minute_job(
    sched: AsyncIOScheduler,
    *, cache,  # RedisCache, 类型为避免循环 import 不加 hint
) -> None:
    """index_minute: 交易时段每 30s, 非交易时段也每 5min 一次 (简化为统一 30s)。"""
    from apps.collector.jobs.index_minute import refresh_all_indices
    sched.add_job(
        refresh_all_indices, IntervalTrigger(seconds=30),
        args=(cache,),
        id="index_minute:ashare", max_instances=1, coalesce=True,
        misfire_grace_time=20,
    )
    log.info("scheduler.index_minute_attached")
```

- [ ] **Step 7: 在 apps/collector/main.py 注册新 job**

在 `attach_us_signal_jobs(...)` 之后、`sched.start()` 之前加:

```python
from core.scheduler.scheduler import attach_index_minute_job  # 顶部 import
attach_index_minute_job(sched, cache=_redis_cache)
```

(注意 `_redis_cache` 已在 Task 7 引入)

- [ ] **Step 8: import smoke + 跑 271+ test 验证无 regression**

```bash
. .venv/bin/activate && python -c "from apps.collector.main import app; print('OK')"
. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -5
```
期望: OK, 测试全 pass.

- [ ] **Step 9: Commit**

```bash
git add apps/collector/jobs/__init__.py apps/collector/jobs/index_minute.py \
        tests/unit/collector/jobs/__init__.py tests/unit/collector/jobs/test_index_minute.py \
        core/scheduler/scheduler.py apps/collector/main.py
git commit -m "feat(collector): index_minute job — 8 大指数 5m 序列预拉取 → Redis cache"
```

---

## Task 9: collector job — market_dashboard (大盘聚合包)

**Files:**
- Create: `apps/collector/jobs/market_dashboard.py`
- Create: `tests/unit/collector/jobs/test_market_dashboard.py`
- Modify: `core/scheduler/scheduler.py` 加 `attach_market_dashboard_job`
- Modify: `apps/collector/main.py` 注册新 job

- [ ] **Step 1: 失败测试**

`tests/unit/collector/jobs/test_market_dashboard.py`:

```python
import pytest
import fakeredis.aioredis

from core.cache import keys
from core.cache.redis_client import RedisCache
from apps.collector.jobs.market_dashboard import build_dashboard


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_build_dashboard_aggregates_sections(cache):
    # 先放置一些其他 cache key, 模拟 collector 已经预填的 index/north
    from datetime import datetime, timezone
    await cache.set_msgpack(
        keys.cache_index_minute("000001.SH", days=1),
        {"symbol": "000001.SH", "granularity": "5m",
         "points": [{"ts": "2026-05-27T01:30:00+00:00", "close": 3000.5, "volume": 1000}],
         "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(), "stale": False}},
        ttl_s=90,
    )
    payload = await build_dashboard("ashare", cache=cache)
    assert payload["market"] == "ashare"
    assert "indices" in payload
    assert "meta" in payload
    assert payload["meta"]["stale"] is False or payload["meta"]["missing_sections"]


async def test_build_dashboard_marks_missing_sections(cache):
    # cache 完全空 — 所有 section 都缺
    payload = await build_dashboard("ashare", cache=cache)
    assert "indices" in payload["meta"]["missing_sections"]
```

- [ ] **Step 2: 实现 apps/collector/jobs/market_dashboard.py**

```python
"""市场 dashboard 聚合包 — 把"前端 /market 页所需的全部数据"打成一个 cache key。

读取已经被其他 job 预填的 cache (cache:index:*:minute), 组装成
cache:market:ashare:dashboard 给 api 单次返回。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §3.3
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from apps.collector.jobs.index_minute import INDEX_SYMBOLS

log = structlog.get_logger(__name__)

_CACHE_TTL_S = 120


async def build_dashboard(market: str, *, cache: RedisCache) -> dict:
    """组合多个 cache 片段 → 完整 dashboard。

    本 Plan 范围:仅 indices 这一段先做出来, 后续 section (overview, north_flow,
    hot_sectors) 在前端切换到 dashboard 接口后逐步加 (留 stub)。
    """
    indices = []
    missing = []
    for sym in INDEX_SYMBOLS:
        payload = await cache.get_msgpack(keys.cache_index_minute(sym, days=1))
        if payload is None:
            continue
        indices.append({
            "symbol": payload["symbol"],
            "granularity": payload.get("granularity", "5m"),
            "points": payload.get("points", []),
        })
    if not indices:
        missing.append("indices")

    payload = {
        "market": market,
        "indices": indices,
        "overview": None,    # 留待后续 plan 填
        "north_flow": None,  # 同上
        "hot_sectors": None, # 同上
        "meta": {
            "fresh_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
            "missing_sections": missing,
        },
    }
    await cache.set_msgpack(keys.cache_market_dashboard(market), payload, ttl_s=_CACHE_TTL_S)
    log.info("dashboard.cached", market=market,
             indices=len(indices), missing=missing)
    return payload


async def refresh_dashboard_job(cache: RedisCache) -> None:
    """APScheduler 调用入口 — 目前只刷 A 股 dashboard。"""
    try:
        await build_dashboard("ashare", cache=cache)
    except Exception as e:  # noqa: BLE001
        log.warning("dashboard.refresh_failed", market="ashare", error=str(e))
```

- [ ] **Step 3: 跑测试 PASS**

期望: 2 个 PASS.

- [ ] **Step 4: 改 core/scheduler/scheduler.py 加 attach 函数**

末尾追加:

```python
def attach_market_dashboard_job(
    sched: AsyncIOScheduler, *, cache,
) -> None:
    from apps.collector.jobs.market_dashboard import refresh_dashboard_job
    sched.add_job(
        refresh_dashboard_job, IntervalTrigger(seconds=60),
        args=(cache,),
        id="market_dashboard:ashare", max_instances=1, coalesce=True,
        misfire_grace_time=30,
    )
    log.info("scheduler.market_dashboard_attached")
```

- [ ] **Step 5: apps/collector/main.py 注册**

加 import + attach 调用 (与 Task 8 同样的位置):

```python
from core.scheduler.scheduler import attach_market_dashboard_job
...
attach_market_dashboard_job(sched, cache=_redis_cache)
```

- [ ] **Step 6: smoke + Commit**

```bash
. .venv/bin/activate && python -c "from apps.collector.main import app; print('OK')"
. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -5
git add apps/collector/jobs/market_dashboard.py \
        tests/unit/collector/jobs/test_market_dashboard.py \
        core/scheduler/scheduler.py apps/collector/main.py
git commit -m "feat(collector): market_dashboard job — A 股大盘聚合包 → Redis cache"
```

---

## Task 10: collector job — refill_consumer (订阅 bus:bars.refill_request)

**Files:**
- Create: `apps/collector/jobs/refill_consumer.py`
- Create: `tests/unit/collector/jobs/test_refill_consumer.py`
- Modify: `apps/collector/main.py` 启动 refill consumer 后台 task

这个 job 是给未来 (Plan 3) 的 read 路径用 — api 发现 cache miss 时 publish 到 `bus:bars.refill_request`, collector 后台消费, 拉数据写库 + 写 cache + 发 `bus:bars.updated`。

Plan 2 范围内仅搭起架子, 不在 api 侧引入对应的 publish 逻辑 (Plan 3 才接)。

- [ ] **Step 1: 失败测试**

`tests/unit/collector/jobs/test_refill_consumer.py`:

```python
import asyncio
import json

import pytest
import fakeredis.aioredis

from core.cache import keys
from apps.collector.jobs.refill_consumer import handle_refill_message


async def test_handle_refill_message_calls_kline_service(monkeypatch):
    called = []

    async def fake_refill(market, symbol, interval, days):
        called.append((market, symbol, interval, days))

    msg = {"market": "ashare", "symbol": "600519.SH", "interval": "1d", "days": 365}
    await handle_refill_message(msg, refill_fn=fake_refill)
    assert called == [("ashare", "600519.SH", "1d", 365)]


async def test_handle_refill_message_swallows_handler_errors(monkeypatch):
    async def fake_refill(*args, **kwargs):
        raise RuntimeError("boom")

    msg = {"market": "ashare", "symbol": "X.SH", "interval": "1d", "days": 30}
    # 不应抛
    await handle_refill_message(msg, refill_fn=fake_refill)


async def test_handle_refill_message_skips_malformed():
    async def fake_refill(*args, **kwargs):
        raise AssertionError("should not be called")

    await handle_refill_message({}, refill_fn=fake_refill)
    await handle_refill_message({"market": "ashare"}, refill_fn=fake_refill)
```

- [ ] **Step 2: 实现 apps/collector/jobs/refill_consumer.py**

```python
"""bus:bars.refill_request 消费者 — Plan 3 read-path 用。

api 发现 cache miss + DB 不全时 publish 到 bus, collector 在此消费, 拉数据
写库 + 写 cache + 发 bus:bars.updated。

Plan 2 仅搭骨架; refill_fn 当前用 KLineService.fetch_fresh_bars 兜, Plan 3 再优化。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §2.3
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)

# Consumer group 名(用同一个 group 跨节点负载均衡)
_GROUP = "collector"
_CONSUMER_PREFIX = "refill"


RefillFn = Callable[[str, str, str, int], Awaitable[None]]
# (market, symbol, interval, days) -> None


async def handle_refill_message(message: dict, *, refill_fn: RefillFn) -> None:
    """处理一条 refill 请求。malformed/失败 不抛, 仅日志。"""
    try:
        market = message["market"]
        symbol = message["symbol"]
        interval = message["interval"]
        days = int(message.get("days", 365))
    except (KeyError, TypeError, ValueError) as e:
        log.warning("refill.malformed", message=message, error=str(e))
        return
    try:
        await refill_fn(market, symbol, interval, days)
        log.info("refill.done", market=market, symbol=symbol, interval=interval, days=days)
    except Exception as e:  # noqa: BLE001
        log.warning("refill.failed", market=market, symbol=symbol,
                    interval=interval, error=str(e))


async def ensure_group(redis: AsyncRedis, stream: str) -> None:
    """确保 consumer group 存在 (BUSYGROUP 表示已存在, 静默忽略)。"""
    try:
        await redis.xgroup_create(stream, _GROUP, id="$", mkstream=True)
    except Exception as e:  # noqa: BLE001
        if "BUSYGROUP" in str(e):
            return
        raise


async def consume_loop(
    redis: AsyncRedis,
    *,
    consumer_id: str,
    refill_fn: RefillFn,
    block_ms: int = 5000,
) -> None:
    """长循环, 阻塞读 stream。被 cancel 时清退。"""
    stream = keys.BUS_BARS_REFILL_REQUEST
    await ensure_group(redis, stream)
    log.info("refill_consumer.start", consumer=consumer_id, stream=stream)
    while True:
        try:
            entries = await redis.xreadgroup(
                _GROUP, consumer_id, streams={stream: ">"},
                count=10, block=block_ms,
            )
        except asyncio.CancelledError:
            log.info("refill_consumer.cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("refill_consumer.read_failed", error=str(e))
            await asyncio.sleep(1)
            continue
        if not entries:
            continue
        for _stream, msgs in entries:
            for msg_id, fields in msgs:
                try:
                    raw = fields.get(b"data") if isinstance(fields, dict) else None
                    if raw is None:
                        log.warning("refill_consumer.missing_data_field", msg_id=msg_id)
                        await redis.xack(stream, _GROUP, msg_id)
                        continue
                    payload = json.loads(raw)
                    await handle_refill_message(payload, refill_fn=refill_fn)
                finally:
                    await redis.xack(stream, _GROUP, msg_id)
```

- [ ] **Step 3: 跑测试 PASS**

期望: 3 个 PASS.

- [ ] **Step 4: 在 apps/collector/main.py 启动 consumer task**

在 leader bootstrap 之后、yield 之前加:

```python
from apps.collector.jobs.refill_consumer import consume_loop

# refill_fn: 当前用 KLineService.fetch_fresh_bars 简单兜 (Plan 3 优化)
kline = get_kline_service()

async def _refill_dispatch(market, symbol, interval, days):
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    await kline.fetch_fresh_bars(symbol, interval=interval, start=start, end=end)

_refill_task = asyncio.create_task(
    consume_loop(_redis_for_mw, consumer_id=f"refill-{os.getpid()}",
                 refill_fn=_refill_dispatch),
)
log.info("refill_consumer.bootstrapped")
```

在 finally 块 (与 leader 相关清理同位置) 加:
```python
        _refill_task.cancel()
        try:
            await _refill_task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 5: smoke + Commit**

```bash
. .venv/bin/activate && python -c "from apps.collector.main import app; print('OK')"
. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -5
git add apps/collector/jobs/refill_consumer.py \
        tests/unit/collector/jobs/test_refill_consumer.py \
        apps/collector/main.py
git commit -m "feat(collector): refill_consumer — 订阅 bus:bars.refill_request 后台消费"
```

---

## Task 11: chip_summary 改为日终预热 + cron 频率审计

**Files:**
- Modify: `core/scheduler/fundamentals_jobs.py` (ff:north 1min → 2min)
- Modify: `core/scheduler/scheduler.py` (attach_fundamentals_jobs 调整)
- Modify: `core/services/chip_service.py` 加 `preload_watchlist_chip_summary()` 方法
- Modify: `apps/collector/main.py` 注册 chip preload cron

- [ ] **Step 1: ff:north 1min → 2min**

修改 `core/scheduler/scheduler.py::attach_fundamentals_jobs`, 把 `IntervalTrigger(minutes=1)` 改成 `IntervalTrigger(minutes=2)`。

- [ ] **Step 2: 在 ChipService 加 preload 方法**

读现有 `core/services/chip_service.py`, 找到合适位置追加:

```python
async def preload_watchlist_chip_summary(
    self, watchlist, *, days: int = 90,
) -> int:
    """日终全量预热: 把 watchlist 中所有 A 股标的的筹码摘要一次性算完写库。

    返回成功 preload 的标的数。单条失败不影响后续。
    """
    syms = await watchlist.dynamic_universe()
    ashare = [s for s in syms if s.endswith(".SH") or s.endswith(".SZ")]
    ok_count = 0
    for s in ashare:
        try:
            await self.get_summary(s, days=days)
            ok_count += 1
        except Exception as e:  # noqa: BLE001
            self._log.warning("chip.preload_failed", symbol=s, error=str(e))
    self._log.info("chip.preload_done", total=len(ashare), ok=ok_count)
    return ok_count
```

(如果 ChipService 用 `log` 全局而不是 `self._log`, 改成对应的)

- [ ] **Step 3: 在 scheduler.py 加 chip preload attach 函数**

末尾追加:

```python
def attach_chip_preload_job(
    sched: AsyncIOScheduler,
    *, chip_service, watchlist,
) -> None:
    """A 股收盘后 15:35 (BJT) 全量预热筹码摘要。"""
    async def _job():
        await chip_service.preload_watchlist_chip_summary(watchlist)
    sched.add_job(
        _job, CronTrigger(hour=7, minute=35),  # 15:35 BJT = 07:35 UTC
        id="chip:preload", max_instances=1, coalesce=True,
        misfire_grace_time=600,
    )
    log.info("scheduler.chip_preload_attached")
```

- [ ] **Step 4: 注册到 collector main.py**

```python
from core.scheduler.scheduler import attach_chip_preload_job
from apps.api.deps import get_chip_service
...
attach_chip_preload_job(sched, chip_service=get_chip_service(),
                        watchlist=get_watchlist_service())
```

- [ ] **Step 5: smoke + 跑测试**

```bash
. .venv/bin/activate && python -c "from apps.collector.main import app; print('OK')"
. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -5
```
期望: OK, 全 pass.

- [ ] **Step 6: Commit**

```bash
git add core/scheduler/scheduler.py core/services/chip_service.py apps/collector/main.py
git commit -m "feat(collector): chip preload 日终 15:35 + ff:north 1min→2min"
```

---

## Task 12: e2e 集成验证

**Files:**
- 不改代码,只是验证 + commit 验证记录到 TODO.md(如有更新)

- [ ] **Step 1: 跑所有非集成测试**

```bash
. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -5
```
期望: 全 pass.

- [ ] **Step 2: 重启服务全套**

```bash
pkill -9 -f "apps.collector.main" 2>/dev/null
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null
sleep 2
docker compose -f docker-compose.dev.yml up -d redis
> /tmp/collector.log
> /tmp/api.log
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 12
```

- [ ] **Step 3: 验证启动日志关键事件**

```bash
grep -E "ak_middleware.ready|leader.acquired|scheduler.built|collector.started|index_minute.cached|dashboard.cached|refill_consumer.start" /tmp/collector.log
```
期望: 至少看到前 4 个 (后 3 个要等 30s/60s 才出)

- [ ] **Step 4: 验证 Redis 状态 keys**

```bash
docker exec marketpulse-redis-dev redis-cli keys "*"
```
期望: 至少看到:
- `state:leader:collector` (有值)
- 几个 `ratelimit:source:*` (rate limiter 在跑后会留 state)
- 不久后 (30s 内) 会看到 `cache:index:000001.SH:minute:1` 等

- [ ] **Step 5: 等 90s, 验证 index_minute cache 已写**

```bash
sleep 90
docker exec marketpulse-redis-dev redis-cli ttl "cache:index:000001.SH:minute:1"
docker exec marketpulse-redis-dev redis-cli strlen "cache:index:000001.SH:minute:1"
```
期望: TTL > 0 (90 左右), strlen > 100 (msgpack payload).

如果该 key 为 nil → 看 collector 日志 grep `index_minute.fetch_failed`, 大概率 sina 接口失败 (国内网络问题 / 反爬), **这是预期降级行为不应阻塞 Plan 2 验收**, 但要把 `index_minute.fetch_failed` 写进 verification report.

- [ ] **Step 6: 验证 dashboard cache**

```bash
sleep 60
docker exec marketpulse-redis-dev redis-cli ttl "cache:market:ashare:dashboard"
```
期望: TTL > 0.

- [ ] **Step 7: 模拟 collector 故障 — kill 后看 leader 自然释放**

```bash
COLLECTOR_PID=$(pgrep -f "apps.collector.main" | head -1)
echo "collector PID: $COLLECTOR_PID"
kill -9 $COLLECTOR_PID
sleep 20
docker exec marketpulse-redis-dev redis-cli get "state:leader:collector"
# 期望: 已过期 = (nil) (TTL=15s, 等 20s 后过期)
```

- [ ] **Step 8: 重启 collector 验证 leader 恢复**

```bash
> /tmp/collector.log
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
sleep 10
grep "leader.acquired" /tmp/collector.log
docker exec marketpulse-redis-dev redis-cli get "state:leader:collector"
```
期望: leader.acquired 出现, Redis key 又有值.

- [ ] **Step 9: 在 TODO.md 标记 Plan 2 完成**

(如 docs/TODO.md 存在; 之前 Plan 1 已经在 docs/TODO.md 写过"已知退化"区, 在那里新加一行)

```bash
grep "Plan 2" docs/TODO.md || echo "(无 Plan 2 标注, 跳过)"
```
若需要更新, 加一行:
> 2026-05-27 ✅ Plan 2 完成:ak_call 三层中间件 + Leader + index_minute/dashboard/refill_consumer job。chip preload 日终 15:35 + ff:north 降到 2min。

- [ ] **Step 10: 完工 commit (如有 docs 改动)**

```bash
git status --short
# 如有 docs/TODO.md 改动:
git add docs/TODO.md
git commit -m "docs(todo): 记录 Plan 2 完成"
```

---

## 验收清单

执行完所有 Task 后, 逐项确认:

- [ ] `pytest -m "not integration"` 全 PASS (270+ tests)
- [ ] collector 启动日志含 `ak_middleware.ready` + `leader.acquired` + `scheduler.built` + `refill_consumer.start`
- [ ] Redis 含 `state:leader:collector` (with TTL)
- [ ] Redis 含 `cache:index:*:minute:1` (90s 内出现)
- [ ] Redis 含 `cache:market:ashare:dashboard` (60s 内出现)
- [ ] Redis 含 `ratelimit:source:sina|em|ths` (有过调用就会出现)
- [ ] Kill collector 后 Redis leader key TTL 过期, 重启 collector 后 leader 重新 acquired
- [ ] `apps/api/routes/` 仍未有 ak_call 调用(Plan 3 才改)— grep 验证
- [ ] CLAUDE.md / spec / TODO.md 在仓库中未冲突

---

## 不在 Plan 2 范围(留 Plan 3)

- ❌ api 路由切到 cache(`indices.py`、`symbols.py:bars`) — Plan 3
- ❌ 前端 stale meta 染灰 — Plan 3
- ❌ `ak_call_with_fallback` 多源 fallback — 5~10 个关键 caller 重构, 列入 docs/TODO.md, 单独 Plan
- ❌ Outlet 商业代理池接入 — 等用户实际遭遇 IP ban 再做
- ❌ 跨进程 QuoteCache 修复 — Plan 3 Stage 5 顺手做

---

## 风险与回滚

| 风险 | 触发 | 回滚 |
|---|---|---|
| Task 6 ak_call 改动后 sina 接口大面积失败 | breaker 把 sina 关停 | env `DISABLE_AK_MIDDLEWARE=1` (后续可以加) 或直接 `git revert <Task 6 SHA>` |
| ratelimit Lua 脚本 bug 阻塞所有 ak_call | RedisTokenBucket 死循环 | revert Task 2; 临时方案: 把 `_ratelimits` 字典清空,容器 setup 时不注入 |
| collector 启动卡在 leader 抢锁 | Redis 网络异常 | leader.try_acquire_once 内 catch + 继续运行 (degrade), 不阻塞 |
| chip_preload 在 15:35 BJT 跑挂 collector | 大量 ak_call 排队 | breaker 会自动熔断,日志可见 — 不需手工干预 |
| Task 7 启动期初始化太慢 collector 超时 | OutletPool / Breaker / Leader 都要 Redis | Redis 不可用时全部 degrade 到无中间件状态, ak_call 直通 |

---

## 下一步

Plan 2 review + 执行完毕后:
- **Plan 3**:api 路由切到 cache + 前端 stale 染灰 + QuoteCache 跨进程修复


