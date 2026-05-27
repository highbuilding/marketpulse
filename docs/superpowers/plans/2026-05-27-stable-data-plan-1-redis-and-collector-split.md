# Plan 1 — Redis 基建 + collector 进程拆分

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 起 Redis (docker-compose) + 封装异步客户端 + 把 APScheduler 从 api 进程搬到独立的 collector 进程, 由 honcho 拉起。完成后 api 重启不影响采集, 采集崩溃不影响 api, 但**所有业务行为零变化**。

**Architecture:** 三步 — (1) Redis docker-compose + Python 客户端封装; (2) 新增 `apps/collector/main.py` entrypoint, 把 `lifespan` 中 scheduler 的 build/attach 逻辑搬过去; (3) `apps/api/main.py` 移除 scheduler 代码, `Procfile` + honcho 统一拉起 redis+collector+api+web。

**Tech Stack:** Python 3.12 / FastAPI / APScheduler / redis-py 5.x asyncio / ormsgpack / honcho / docker-compose

**Spec reference:** `docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md` §1, §2.1-2.2, §6.1-6.2

---

## File Structure

### 新增
- `docker-compose.dev.yml` — 本地 Redis 7-alpine 容器
- `Procfile` — honcho 启动定义
- `core/cache/redis_client.py` — async Redis 单例 + msgpack get/set 包装
- `core/cache/keys.py` — key 命名常量与构造函数, 集中管理 `cache:`/`bus:`/`state:`/`ratelimit:` 前缀
- `apps/collector/__init__.py` — 空文件
- `apps/collector/main.py` — collector 进程 entrypoint, 跑 scheduler + `/health`
- `tests/unit/cache/__init__.py` — 空文件
- `tests/unit/cache/test_keys.py` — key 构造单测
- `tests/unit/cache/test_redis_client.py` — 客户端单测 (用 fakeredis-py)

### 修改
- `pyproject.toml` — 加 `redis>=5.0`, `ormsgpack>=1.4`, `honcho>=1.1`; dev 加 `fakeredis>=2.20`
- `apps/api/main.py` — `lifespan` 移除 scheduler build/attach/start/shutdown; 启动时连 Redis 客户端 (失败仅 warning, 不阻塞)
- `Makefile` — `dev` 目标改为 `honcho start -f Procfile`
- `CLAUDE.md` — 雷区 2 重启模板加上 collector

---

## Task 1: 加依赖 + Redis docker-compose

**Files:**
- Modify: `pyproject.toml`
- Create: `docker-compose.dev.yml`

- [ ] **Step 1: 加 Python 依赖**

修改 `pyproject.toml`:

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "apscheduler>=3.10",
    "duckdb>=1.1",
    "aiosqlite>=0.20",
    "akshare>=1.15",
    "mootdx>=0.11",
    "yfinance>=1.0",
    "curl-cffi>=0.7",
    "alpaca-py>=0.33",
    "websockets>=13",
    "httpx>=0.25",
    "structlog>=24.4",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "pyyaml>=6.0",
    "tenacity>=8.1",
    "redis>=5.0",
    "ormsgpack>=1.4",
    "honcho>=1.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-mock>=3.14",
    "respx>=0.21",
    "ruff>=0.7",
    "mypy>=1.13",
    "fakeredis>=2.20",
]
```

- [ ] **Step 2: 安装依赖**

Run: `. .venv/bin/activate && pip install -e ".[dev]"`
Expected: 安装 redis / ormsgpack / honcho / fakeredis, 无 conflict.

- [ ] **Step 3: 创建 docker-compose.dev.yml**

```yaml
# docker-compose.dev.yml
# 本地 dev 用,生产请用 systemd 管理 redis-server 或托管服务。
services:
  redis:
    image: redis:7-alpine
    container_name: marketpulse-redis-dev
    ports:
      - "127.0.0.1:6379:6379"
    command: >
      redis-server
      --appendonly no
      --save ""
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
```

说明:
- `--save ""` + `--appendonly no`:不持久化 (我们的 spec §6.7 明确不需要,Redis 仅作缓存层)
- `maxmemory 512mb + allkeys-lru`:满了自动驱逐最久未用 key
- 仅监听 127.0.0.1:不暴露到外网

- [ ] **Step 4: 启动 Redis 验证**

Run: `docker compose -f docker-compose.dev.yml up -d redis && sleep 2 && docker exec marketpulse-redis-dev redis-cli ping`
Expected: `PONG`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docker-compose.dev.yml
git commit -m "chore(deps): 加 redis/ormsgpack/honcho 依赖 + Redis docker-compose"
```

---

## Task 2: key 命名常量模块 + 单测

**Files:**
- Create: `core/cache/__init__.py` (确认存在; 如已存在则跳过)
- Create: `core/cache/keys.py`
- Create: `tests/unit/cache/__init__.py`
- Create: `tests/unit/cache/test_keys.py`

- [ ] **Step 1: 检查 core/cache/__init__.py 是否已存在**

Run: `ls core/cache/__init__.py 2>/dev/null && echo EXISTS || echo MISSING`
- 若 EXISTS:跳过创建
- 若 MISSING:创建空文件 `touch core/cache/__init__.py`

- [ ] **Step 2: 写 test_keys.py 失败测试**

```python
# tests/unit/cache/test_keys.py
import pytest

from core.cache import keys


def test_cache_quote_key():
    assert keys.cache_quote("ashare", "600519.SH") == "cache:quote:ashare:600519.SH"


def test_cache_index_minute_key():
    assert keys.cache_index_minute("000001.SH", days=1) == "cache:index:000001.SH:minute:1"
    assert keys.cache_index_minute("000001.SH", days=5) == "cache:index:000001.SH:minute:5"


def test_cache_market_dashboard_key():
    assert keys.cache_market_dashboard("ashare") == "cache:market:ashare:dashboard"


def test_cache_bars_tail_key():
    assert (
        keys.cache_bars_tail("ashare", "600519.SH", "1d")
        == "cache:bars:ashare:600519.SH:1d:tail"
    )


def test_state_leader_collector_key():
    assert keys.state_leader_collector() == "state:leader:collector"


def test_state_source_key():
    assert keys.state_source("sina") == "state:source:sina"


def test_state_outlet_key():
    assert keys.state_outlet("local") == "state:outlet:local"


def test_state_inflight_key():
    assert keys.state_inflight("bars:600519.SH:1d") == "state:inflight:bars:600519.SH:1d"


def test_ratelimit_source_key():
    assert keys.ratelimit_source("sina") == "ratelimit:source:sina"


def test_bus_topic_constants():
    assert keys.BUS_QUOTE_TICK == "bus:quote.tick"
    assert keys.BUS_BARS_UPDATED == "bus:bars.updated"
    assert keys.BUS_SIGNAL_NEW == "bus:signal.new"
    assert keys.BUS_SOURCE_STATUS == "bus:source.status"
    assert keys.BUS_BARS_REFILL_REQUEST == "bus:bars.refill_request"


def test_validate_key_rejects_single_segment():
    with pytest.raises(ValueError, match="must be at least 2 segments"):
        keys.validate("foo")


def test_validate_key_rejects_unknown_namespace():
    with pytest.raises(ValueError, match="unknown namespace"):
        keys.validate("foobar:xxx")


def test_validate_key_accepts_well_formed():
    keys.validate("cache:quote:ashare:600519.SH")
    keys.validate("state:source:sina")
    keys.validate("bus:quote.tick")
    keys.validate("ratelimit:source:sina")
```

- [ ] **Step 3: 跑测试确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/cache/test_keys.py -v`
Expected: ImportError 或所有测试 FAIL (`core.cache.keys` 还没实现)

- [ ] **Step 4: 实现 core/cache/keys.py**

```python
# core/cache/keys.py
"""Redis key 命名空间集中定义。

所有 Redis key 必须经过这里的构造函数,直接拼字符串视为违规。
key 必须至少 2 段 (namespace:scope),namespace 限定为
{cache, state, bus, ratelimit} 四个之一。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §2.1
"""
from __future__ import annotations

# === 命名空间 ===
NS_CACHE = "cache"
NS_STATE = "state"
NS_BUS = "bus"
NS_RATELIMIT = "ratelimit"
_VALID_NS = {NS_CACHE, NS_STATE, NS_BUS, NS_RATELIMIT}

# === bus topic 常量(Streams 名) ===
BUS_QUOTE_TICK = "bus:quote.tick"
BUS_BARS_UPDATED = "bus:bars.updated"
BUS_SIGNAL_NEW = "bus:signal.new"
BUS_SOURCE_STATUS = "bus:source.status"
BUS_BARS_REFILL_REQUEST = "bus:bars.refill_request"


# === cache: 热缓存层 ===
def cache_quote(market: str, symbol: str) -> str:
    return f"cache:quote:{market}:{symbol}"


def cache_index_minute(symbol: str, *, days: int) -> str:
    return f"cache:index:{symbol}:minute:{days}"


def cache_market_dashboard(market: str) -> str:
    return f"cache:market:{market}:dashboard"


def cache_bars_tail(market: str, symbol: str, interval: str) -> str:
    return f"cache:bars:{market}:{symbol}:{interval}:tail"


def cache_bars_full(market: str, symbol: str, interval: str, fingerprint: str) -> str:
    return f"cache:bars:{market}:{symbol}:{interval}:full:{fingerprint}"


def cache_fundflow(symbol: str, *, days: int) -> str:
    return f"cache:fundflow:{symbol}:{days}d"


# === state: 状态/锁 ===
def state_leader_collector() -> str:
    return "state:leader:collector"


def state_source(name: str) -> str:
    return f"state:source:{name}"


def state_outlet(outlet_id: str) -> str:
    return f"state:outlet:{outlet_id}"


def state_inflight(key: str) -> str:
    return f"state:inflight:{key}"


# === ratelimit: 限速器 ===
def ratelimit_source(source: str) -> str:
    return f"ratelimit:source:{source}"


def ratelimit_outlet(outlet_id: str) -> str:
    return f"ratelimit:outlet:{outlet_id}"


# === 校验 ===
def validate(key: str) -> None:
    """校验 key 命名规范。违规 raise ValueError。

    用于 redis_client 写入前的 assertion,catch 散点拼字符串。
    """
    parts = key.split(":")
    if len(parts) < 2:
        raise ValueError(f"key must be at least 2 segments: {key!r}")
    ns = parts[0]
    if ns not in _VALID_NS:
        raise ValueError(f"unknown namespace {ns!r} in key {key!r}; "
                         f"allowed: {sorted(_VALID_NS)}")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/cache/test_keys.py -v`
Expected: 所有测试 PASS

- [ ] **Step 6: Commit**

```bash
git add core/cache/keys.py tests/unit/cache/__init__.py tests/unit/cache/test_keys.py
git commit -m "feat(cache): 加 Redis key 命名常量模块 + 单测"
```

---

## Task 3: Redis 异步客户端封装

**Files:**
- Create: `core/cache/redis_client.py`
- Create: `tests/unit/cache/test_redis_client.py`

- [ ] **Step 1: 写 test_redis_client.py 失败测试**

```python
# tests/unit/cache/test_redis_client.py
import pytest
import fakeredis.aioredis

from core.cache import keys
from core.cache.redis_client import RedisCache


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_set_and_get_msgpack_roundtrip(cache):
    payload = {"symbol": "600519.SH", "price": 1234.56, "ts": "2026-05-27T08:00:00Z"}
    await cache.set_msgpack(keys.cache_quote("ashare", "600519.SH"), payload, ttl_s=60)
    got = await cache.get_msgpack(keys.cache_quote("ashare", "600519.SH"))
    assert got == payload


async def test_get_msgpack_missing_returns_none(cache):
    got = await cache.get_msgpack(keys.cache_quote("ashare", "NOTEXIST.SH"))
    assert got is None


async def test_set_msgpack_validates_key(cache):
    with pytest.raises(ValueError):
        await cache.set_msgpack("invalid_key_no_namespace", {"x": 1}, ttl_s=60)


async def test_set_msgpack_requires_positive_ttl(cache):
    with pytest.raises(ValueError, match="ttl_s must be > 0"):
        await cache.set_msgpack(keys.cache_quote("ashare", "X.SH"), {}, ttl_s=0)


async def test_ttl_is_set(cache):
    key = keys.cache_quote("ashare", "TTL.SH")
    await cache.set_msgpack(key, {"x": 1}, ttl_s=30)
    ttl = await cache.ttl(key)
    assert 25 < ttl <= 30


async def test_ping(cache):
    assert await cache.ping() is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/cache/test_redis_client.py -v`
Expected: ImportError on `RedisCache`

- [ ] **Step 3: 实现 core/cache/redis_client.py**

```python
# core/cache/redis_client.py
"""Async Redis 客户端封装。

提供 msgpack 编解码 + key 命名校验 + ping 健康检查。
所有热缓存读写经过这里,绝不直接 .get/.set 字符串。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §2.6
"""
from __future__ import annotations

from typing import Any

import ormsgpack
import structlog
from redis.asyncio import Redis as AsyncRedis

from core.cache import keys

log = structlog.get_logger(__name__)


class RedisCache:
    """读写均走 msgpack 序列化。Redis 客户端注入,方便测试用 fakeredis 替换。"""

    def __init__(self, redis: AsyncRedis) -> None:
        self._r = redis

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception as e:  # noqa: BLE001
            log.warning("redis.ping_failed", error=str(e))
            return False

    async def get_msgpack(self, key: str) -> Any | None:
        keys.validate(key)
        raw = await self._r.get(key)
        if raw is None:
            return None
        return ormsgpack.unpackb(raw)

    async def set_msgpack(self, key: str, value: Any, *, ttl_s: int) -> None:
        keys.validate(key)
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be > 0, got {ttl_s}")
        raw = ormsgpack.packb(value)
        await self._r.set(key, raw, ex=ttl_s)

    async def ttl(self, key: str) -> int:
        keys.validate(key)
        return int(await self._r.ttl(key))

    async def delete(self, key: str) -> None:
        keys.validate(key)
        await self._r.delete(key)


def make_redis(url: str = "redis://127.0.0.1:6379/0") -> AsyncRedis:
    """单例工厂。生产中由依赖注入使用,测试时直接 new fakeredis。"""
    return AsyncRedis.from_url(url, decode_responses=False)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/cache/test_redis_client.py -v`
Expected: 6 个测试全部 PASS

- [ ] **Step 5: Commit**

```bash
git add core/cache/redis_client.py tests/unit/cache/test_redis_client.py
git commit -m "feat(cache): 加 RedisCache async 客户端封装(msgpack + key 校验)"
```

---

## Task 4: api 启动时连 Redis (失败不阻塞)

**Files:**
- Modify: `apps/api/deps.py` (加 `get_redis_cache` 依赖)
- Modify: `apps/api/main.py` (lifespan 启动时 ping 一次)

- [ ] **Step 1: 看现有 deps.py 末尾几行**

Run: `tail -30 apps/api/deps.py`
记下最后一个依赖函数的格式,新加的 `get_redis_cache` 与之保持风格一致。

- [ ] **Step 2: 在 apps/api/deps.py 末尾追加**

```python
# === Redis cache (Plan 1 stage 1) ===
from core.cache.redis_client import RedisCache, make_redis  # noqa: E402

_redis_cache_singleton: RedisCache | None = None


def get_redis_cache() -> RedisCache:
    global _redis_cache_singleton
    if _redis_cache_singleton is None:
        import os
        url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        _redis_cache_singleton = RedisCache(make_redis(url))
    return _redis_cache_singleton
```

- [ ] **Step 3: 在 apps/api/main.py lifespan 开头加 Redis ping**

在 `apps/api/main.py` 的 `lifespan` 函数,`state_repo = get_state_repo()` 之前加:

```python
    # Plan 1: ping Redis,失败仅 warning 不阻塞 (优雅降级)
    from apps.api.deps import get_redis_cache
    redis_ok = await get_redis_cache().ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup",
                    note="api 将退化到 DB 直读模式,直到 Redis 恢复")
```

- [ ] **Step 4: import smoke test**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; print('OK')"`
Expected: `OK`(无 import 错误)

- [ ] **Step 5: 启动 api + 验证日志**

Run:
```bash
docker compose -f docker-compose.dev.yml up -d redis
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null; sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 6
grep "redis.connected" /tmp/api.log
```
Expected: 输出包含 `redis.connected` 一行

- [ ] **Step 6: 关掉 Redis 验证 fallback 不阻塞**

Run:
```bash
docker compose -f docker-compose.dev.yml stop redis
pkill -9 -f "uvicorn apps.api.main:app"; sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 6
grep "redis.unavailable_at_startup" /tmp/api.log
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8787/api/health
```
Expected: 日志含 `redis.unavailable_at_startup`,健康检查仍 200

- [ ] **Step 7: 重启 Redis 恢复, 提交**

```bash
docker compose -f docker-compose.dev.yml up -d redis
git add apps/api/deps.py apps/api/main.py
git commit -m "feat(api): 启动时连 Redis(失败不阻塞,日志降级提示)"
```

---

## Task 5: 抽出 build_scheduler 配置, 让 collector 直接复用

**Files:**
- Read: `core/scheduler/scheduler.py` (确认 `build_scheduler` / `attach_*` 签名)
- 不改代码, 只是确认现状

- [ ] **Step 1: 确认 scheduler 导出函数签名**

Run: `grep -n "^def " core/scheduler/scheduler.py`
Expected: 至少看到 `build_scheduler`, `attach_fundamentals_jobs`, `attach_signal_jobs`, `attach_us_signal_jobs` 4 个函数

如果列表不止这 4 个,记下完整清单,Task 6 中要全部 attach。

- [ ] **Step 2: 确认 deps.py 提供的 service getter 完整**

Run: `grep "^def get_" apps/api/deps.py`
Expected: 至少看到 `get_registry`, `get_quote_cache`, `get_bar_repo`, `get_watchlist_service`, `get_fund_flow_service`, `get_signal_scan_service`, `get_notification_service`, `get_kline_service`, `get_state_repo`, `get_symbol_directory_service`

如果有遗漏, Task 6 的 collector main.py 中 import 列表要相应调整。

- [ ] **Step 3: 不需要 commit**(只是阅读现状)

---

## Task 6: 写 collector 进程入口

**Files:**
- Create: `apps/collector/__init__.py`
- Create: `apps/collector/main.py`

- [ ] **Step 1: 创建空 `__init__.py`**

Run: `touch apps/collector/__init__.py`

- [ ] **Step 2: 写 apps/collector/main.py**

```python
# apps/collector/main.py
"""Collector 进程入口。

职责:
- 跑 APScheduler (所有 cron / interval 任务,涵盖各市场 tick / flush /
  fundamentals / signal scan)
- 把 ak_call 全部局限在本进程
- 暴露 /health 给运维(8788)

绝对禁止: 暴露任何业务 HTTP 接口 — 那是 apps/api 的职责。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §1, §4.1
"""
from __future__ import annotations

import asyncio
import os
import signal
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from core.integrations.logging_setup import setup_logging
setup_logging()

import structlog
import uvicorn
from fastapi import FastAPI

from apps.api.deps import (
    get_bar_repo, get_fund_flow_service, get_kline_service,
    get_notification_service, get_quote_cache, get_redis_cache, get_registry,
    get_signal_scan_service, get_state_repo, get_symbol_directory_service,
    get_watchlist_service,
)
from core.scheduler.scheduler import (
    attach_fundamentals_jobs, attach_signal_jobs, attach_us_signal_jobs,
    build_scheduler,
)

log = structlog.get_logger(__name__)


async def _async_refresh_directory(svc) -> None:
    """与 apps/api/main.py 中同名函数行为一致。
    雷区 4: stock_zh_a_spot 跑过会污染 V8 状态,启动 5s 后再跑,且只在目录 < 100 行时跑。
    """
    try:
        await asyncio.sleep(5)
        n = await svc.refresh_ashare()
        log.info("directory.bootstrapped", count=n)
    except Exception as e:  # noqa: BLE001
        log.warning("directory.bootstrap_failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("collector.boot")

    redis_ok = await get_redis_cache().ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup",
                    note="collector 将继续运行,熔断/限速降级到内存态")

    state_repo = get_state_repo()
    await state_repo.init()
    await get_watchlist_service().bootstrap_default()

    dir_svc = get_symbol_directory_service()
    await dir_svc.bootstrap_seeds()
    await dir_svc.bootstrap_us_seeds()
    if not os.getenv("MARKETPULSE_SKIP_DIR_BOOTSTRAP"):
        existing = await dir_svc.count()
        if existing < 100:
            asyncio.create_task(_async_refresh_directory(dir_svc))
        else:
            log.info("directory.skip_refresh", existing=existing)

    registry = get_registry()
    cache = get_quote_cache()
    bar_repo = get_bar_repo()

    sched = build_scheduler(registry, cache, bar_repo, get_watchlist_service())
    attach_fundamentals_jobs(
        sched, fund_flow=get_fund_flow_service(),
        watchlist=get_watchlist_service(),
    )
    attach_signal_jobs(
        sched, signal_scan=get_signal_scan_service(),
        watchlist=get_watchlist_service(),
        notify_service=get_notification_service(),
        kline=get_kline_service(),
    )
    attach_us_signal_jobs(
        sched, signal_scan=get_signal_scan_service(),
        watchlist=get_watchlist_service(),
        notify_service=get_notification_service(),
        kline=get_kline_service(),
    )
    sched.start()
    log.info("collector.started", markets=registry.markets())

    try:
        yield
    finally:
        sched.shutdown(wait=False)
        log.info("collector.shutdown")


# 一个最小的 FastAPI app,仅用于 /health(给运维 / honcho 探活)
app = FastAPI(title="MarketPulse Collector", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "role": "collector"}


def main() -> None:
    """uvicorn 入口。用 --host 127.0.0.1 + 内网端口,只暴露给运维。"""
    port = int(os.getenv("COLLECTOR_PORT", "8788"))
    uvicorn.run(
        "apps.collector.main:app",
        host="127.0.0.1",
        port=port,
        log_config=None,  # 沿用 setup_logging() 的 structlog
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: smoke test 确认 import 正常**

Run: `. .venv/bin/activate && python -c "from apps.collector.main import app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: 单独跑一下 collector 验证**

Run:
```bash
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null
pkill -9 -f "apps.collector.main" 2>/dev/null
sleep 2
docker compose -f docker-compose.dev.yml up -d redis
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
sleep 8
grep "collector.started" /tmp/collector.log
curl -s http://localhost:8788/health
```
Expected:
- 日志含 `collector.started`
- /health 返回 `{"status":"ok","role":"collector"}`

- [ ] **Step 5: 关掉 collector 准备下一步**

Run: `pkill -9 -f "apps.collector.main"; sleep 2`

- [ ] **Step 6: Commit**

```bash
git add apps/collector/__init__.py apps/collector/main.py
git commit -m "feat(collector): 新建 collector 进程 entrypoint(scheduler + /health)"
```

---

## Task 7: 从 api 进程移除 scheduler

**Files:**
- Modify: `apps/api/main.py`

- [ ] **Step 1: 改 apps/api/main.py 的 lifespan**

打开 `apps/api/main.py`,找到 `lifespan` 函数。**删除以下部分**:

1. 删除 `_async_refresh_directory` 整个函数(已搬到 collector)
2. 删除 lifespan 中的:
   - `await get_watchlist_service().bootstrap_default()` (collector 处理)
   - 整段 `dir_svc = get_symbol_directory_service()` ... `log.info("directory.skip_refresh", ...)`
   - `registry = get_registry()`
   - `cache = get_quote_cache()`
   - `bar_repo = get_bar_repo()`
   - `sched = build_scheduler(...)`
   - 三个 `attach_*` 调用
   - `sched.start()`
   - `log.info("app.started", ...)`
   - `finally:` 中的 `sched.shutdown(wait=False)` 和 `log.info("app.stopped")`
3. 删除文件顶部 import:`from core.scheduler.scheduler import (...)` 整段

**保留**:
- `state_repo.init()` (api 启动也需要 db schema 就绪 — 双方都跑等幂等)
- Redis ping(Task 4 加的)
- FastAPI app + middleware + include_router

修改后 `lifespan` 应该是这样:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Plan 1: ping Redis,失败仅 warning 不阻塞 (优雅降级)
    from apps.api.deps import get_redis_cache
    redis_ok = await get_redis_cache().ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup",
                    note="api 将退化到 DB 直读模式,直到 Redis 恢复")

    state_repo = get_state_repo()
    await state_repo.init()

    log.info("api.started")
    yield
    log.info("api.stopped")
```

并清理 import 区中不再用的引用:
- `import asyncio` — 看是否还有其他地方用,有就保留
- `from apps.api.deps import (...)` — 缩到只剩实际用的(`get_redis_cache`, `get_state_repo`)
- 移除 `from core.scheduler.scheduler import (...)`

- [ ] **Step 2: import smoke test**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; print('OK')"`
Expected: `OK`

- [ ] **Step 3: 单跑 api 验证 scheduler 真的不在了**

Run:
```bash
docker compose -f docker-compose.dev.yml up -d redis
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null; sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 6
grep -E "api.started|scheduler.built" /tmp/api.log
```
Expected:
- 含 `api.started`
- **不含** `scheduler.built` (这就是 collector 的事)

- [ ] **Step 4: 同时跑 collector + api 验证 e2e**

```bash
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
sleep 8
echo "--- api ---"
grep -E "api.started|scheduler.built" /tmp/api.log
echo "--- collector ---"
grep -E "collector.started|scheduler.built" /tmp/collector.log
echo "--- ports ---"
curl -s -o /dev/null -w "api %{http_code}\n" http://localhost:8787/api/health
curl -s -o /dev/null -w "collector %{http_code}\n" http://localhost:8788/health
```

Expected:
- api 日志:`api.started`,无 `scheduler.built`
- collector 日志:`collector.started` + `scheduler.built`
- 两个端口都 200

- [ ] **Step 5: 收尾,关进程**

```bash
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null
pkill -9 -f "apps.collector.main" 2>/dev/null
sleep 2
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/main.py
git commit -m "refactor(api): scheduler 搬到 collector 进程,api 仅做读路径"
```

---

## Task 8: Procfile + honcho + Makefile dev

**Files:**
- Create: `Procfile`
- Modify: `Makefile`
- Modify: `CLAUDE.md` (雷区 2 重启模板)

- [ ] **Step 1: 创建 Procfile**

```
# Procfile — honcho 拉起本地 dev 多进程
# 用法: make dev (内部调 honcho start)
#
# 注意: redis 通过 docker-compose 单独管理(不在 honcho 里),
#       因为容器生命周期不该被 honcho Ctrl-C 一起干掉。
#
# 启动顺序 honcho 不保证, 但每个进程内部都做了"Redis 不可用时降级"。

collector: . .venv/bin/activate && python -m apps.collector.main
api:       . .venv/bin/activate && uvicorn apps.api.main:app --port 8787
web:       cd apps/web && npm run dev
```

- [ ] **Step 2: 改 Makefile dev 目标**

```makefile
.PHONY: install dev dev-redis dev-stop test test-integration test-full lint typecheck web-install web-dev clean warmup

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

dev-redis:
	docker compose -f docker-compose.dev.yml up -d redis

dev: dev-redis
	# 用 honcho 拉起 collector + api + web (Procfile 定义)
	# Redis 单独由 docker-compose 管理(不进 honcho,Ctrl-C 不会停容器)
	# 雷区 2: 不能加 uvicorn --reload — V8 状态会污染。
	# 代码变更请 Ctrl-C 退出 honcho 再重新 make dev。
	. .venv/bin/activate && honcho start -f Procfile

dev-stop:
	pkill -9 -f "apps.collector.main" 2>/dev/null || true
	pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null || true
	docker compose -f docker-compose.dev.yml stop redis
	@echo "stopped collector / api / redis (web 由 honcho/Ctrl-C 管理)"

test:
	. .venv/bin/activate && pytest -m "not integration"

test-integration:
	. .venv/bin/activate && pytest -m integration

test-full: test test-integration
	cd apps/web && npx playwright test

lint:
	. .venv/bin/activate && ruff check core apps tests

typecheck:
	. .venv/bin/activate && mypy core apps

web-install:
	cd apps/web && npm install

clean:
	rm -rf .venv apps/web/node_modules apps/web/.next data/*.duckdb data/*.db

warmup:
	. .venv/bin/activate && NO_PROXY='*' python -m apps.warmup --from-watchlist --days 365
```

- [ ] **Step 3: 更新 CLAUDE.md 雷区 2 重启模板**

打开 `CLAUDE.md`,找到 **雷区 2** 章节(`### 雷区 2:uvicorn --reload 不安全`),把现有重启模板替换为:

```bash
# 停服务 — 三方都要停 (collector / api 都跑了 ak_call 相关或读 Redis)
pkill -9 -f "apps.collector.main" 2>/dev/null
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null
sleep 2

# Redis 一直跑(由 docker-compose 管理),平时不需要重启它

# ... 干活、改代码、commit、跑测试 ...

# 收尾:重启 collector + api,不要留任何端口空着
docker compose -f docker-compose.dev.yml up -d redis
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 8
curl -s -m 3 http://localhost:8787/api/health | grep -o '"status":"[^"]*"'
curl -s -m 3 http://localhost:8788/health | grep -o '"status":"[^"]*"'
```

并在该章节末尾加一条:

> **2026-05-27 之后**:scheduler 已搬到 collector 进程,任何重启 api 的操作都**不再影响采集**。但反之,collector 崩或重启会停掉所有 cron 任务,务必同步重启。`/tmp/collector.log` 是 collector 的 stdout,事实源仍是 `data/logs/api.log`(structlog 共用)。

- [ ] **Step 4: smoke test honcho**

Run:
```bash
make dev-stop 2>/dev/null
sleep 1
. .venv/bin/activate && honcho start -f Procfile &
HONCHO_PID=$!
sleep 12
curl -s -m 3 http://localhost:8787/api/health
echo
curl -s -m 3 http://localhost:8788/health
echo
kill $HONCHO_PID 2>/dev/null
sleep 2
```

Expected:
- api `{"status":"ok",...}`
- collector `{"status":"ok","role":"collector"}`

- [ ] **Step 5: Commit**

```bash
git add Procfile Makefile CLAUDE.md
git commit -m "feat(dev): Procfile + honcho + Makefile dev/dev-stop + 更新 CLAUDE.md 雷区 2"
```

---

## Task 9: e2e 验证 — 现有功能行为不变

**Files:**
- 不改代码,只是验证 + commit 一个验证记录到 spec / TODO 末尾

- [ ] **Step 1: 跑现有所有单元测试**

Run: `. .venv/bin/activate && pytest -m "not integration" -q`
Expected: 全部 PASS(本 plan 没改业务代码,既有测试不能 fail)

- [ ] **Step 2: 跑 lint + typecheck**

Run: `make lint && make typecheck`
Expected: 0 错误

- [ ] **Step 3: 完整端到端起服务,跑业务冒烟**

```bash
make dev-stop 2>/dev/null
sleep 1
. .venv/bin/activate && honcho start -f Procfile > /tmp/honcho.log 2>&1 &
HONCHO_PID=$!
sleep 15

# 业务冒烟
echo "--- 健康检查 ---"
curl -s http://localhost:8787/api/health
echo
curl -s http://localhost:8788/health
echo

echo "--- 现有路由 (应仍工作, 但路由内部是否调 ak_call 还没改 — Stage 5 才改) ---"
curl -s -m 5 -o /dev/null -w "watchlists: %{http_code}\n" http://localhost:8787/api/watchlists
curl -s -m 5 -o /dev/null -w "cd-signals: %{http_code}\n" "http://localhost:8787/api/cd-signals?limit=10"
curl -s -m 5 -o /dev/null -w "ai_market:  %{http_code}\n" http://localhost:8787/api/ai-market/ashare/dashboard

echo "--- collector 日志确认 scheduler 在跑 ---"
grep -E "scheduler.built|tick:|flush:" /tmp/collector.log | head -5

kill $HONCHO_PID 2>/dev/null
sleep 2
```

Expected:
- 三个健康检查 200
- watchlists / cd-signals 200(应该,这俩是 DB 直读)
- ai_market 路由具体行为看现状 — 200 就 OK,不要在意慢
- collector 日志能看到 scheduler 已经跑起来了

- [ ] **Step 4: 验证 api 重启不影响采集**

```bash
# 用上一步 honcho 起的进程,重启 api 单独验证
make dev-stop
sleep 1
. .venv/bin/activate && honcho start -f Procfile > /tmp/honcho.log 2>&1 &
HONCHO_PID=$!
sleep 15

# 单杀 api
pkill -9 -f "uvicorn apps.api.main:app"
sleep 3

# collector 应该还在
curl -s -o /dev/null -w "collector after api kill: %{http_code}\n" http://localhost:8788/health

# 重启 api
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 6
curl -s -o /dev/null -w "api after restart: %{http_code}\n" http://localhost:8787/api/health

# 收尾
kill $HONCHO_PID 2>/dev/null
make dev-stop
```

Expected:
- collector after api kill: 200(没受影响)
- api after restart: 200

- [ ] **Step 5: 加一条简短验证记录到 docs/TODO.md(如该文件存在)**

Run: `ls docs/TODO.md 2>/dev/null && echo EXISTS || echo MISSING`

- 若 EXISTS:在 TODO.md 顶部 / "已完成项" 区(若有)加一行:

  > 2026-05-27 ✅ Plan 1 完成:Redis 基建 + collector 进程拆分 + honcho。api 重启不影响采集。

- 若 MISSING:跳过本步,Plan 1 不需要新建 TODO.md

- [ ] **Step 6: Commit verification record(如 Step 5 改了文件)**

```bash
git status --short docs/TODO.md
# 如果有改:
git add docs/TODO.md
git commit -m "docs(todo): 记录 Plan 1 完成"
```

---

## 验收清单(整个 Plan 1 完工标准)

执行完所有 Task 后,逐项确认:

- [ ] `make dev` 一键起 redis + collector + api + web,15s 内全部健康
- [ ] `apps/api/main.py` 不再 import `core.scheduler.scheduler`(`grep` 验证)
- [ ] `apps/collector/main.py` 启动日志含 `scheduler.built` + `collector.started`
- [ ] `apps/api/main.py` 启动日志**不含** `scheduler.built`
- [ ] 关 Redis 后 api 启动**仍能成功**(降级 warning 而非 fail)
- [ ] api 单独重启**不影响** collector 进程
- [ ] 现有所有单元测试 + lint + typecheck 全 PASS
- [ ] `core/cache/keys.py::validate` 拒绝 `foo`(无前缀)、`foobar:x`(未知前缀)
- [ ] CLAUDE.md 雷区 2 重启模板已更新含 collector
- [ ] `pyproject.toml` 含 redis / ormsgpack / honcho / fakeredis 依赖

---

## 不在 Plan 1 范围(留给后续 Plan)

- ❌ ak_call 三层中间件(Outlet / Breaker / Ratelimit) — Plan 2
- ❌ collector 新增 job(index_minute / market_dashboard / refill_consumer) — Plan 2
- ❌ api 路由切到 cache(消除 4 个卡顿) — Plan 3
- ❌ 前端 stale meta 染灰 — Plan 3
- ❌ Leader 选举 — Plan 2(单机不影响,但代码 ready)

---

## 风险与回滚

| 风险 | 触发 | 回滚 |
|---|---|---|
| Task 7 后 api 启动失败 | scheduler 删多了或 deps import 链路断 | `git revert HEAD` 恢复 api/main.py;补救后重提 |
| collector 启动后 cron 不跑 | apps/api/deps.py 中 service singleton 在 api/collector 双进程下重复初始化导致 DB 锁冲突 | 排查方向:DuckDB / SQLite 都是 single-writer,但 collector 写、api 只读 — 应无冲突。如冲突立刻 stop api,先验证 collector 单跑正常,再排查 deps.py |
| docker 不可用的开发机 | 本机没装 Docker | 临时方案:`brew install redis && redis-server`(命令行直跑,绕过 docker-compose);Procfile 临时手工注释 redis 行 |
| Procfile 在 macOS 与 Linux 行为不一致 | honcho 跨平台但 shell 行为差异 | 用 `bash -lc '...'` 包裹复杂命令(已用 `. .venv/bin/activate && ...` 形式) |

---

## 下一步

Plan 1 review + 执行完毕后,继续:
- **Plan 2**:ak_call 三层中间件(Outlet / Breaker / Ratelimit) + Leader + collector 新增 job
- **Plan 3**:api 路由切到 cache + 前端 stale 染灰
