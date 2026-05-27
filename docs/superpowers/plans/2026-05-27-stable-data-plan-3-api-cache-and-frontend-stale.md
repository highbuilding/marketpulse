# Plan 3 — api 切 cache + 前端 stale 染灰 + 收尾退化修复

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 api 路由的读路径全部切到 Redis cache(消除最后的 ak_call 入口), 修 Plan 1 引入的 QuoteCache 跨进程退化, 前端读 `meta.stale` 染灰, Plan 2 留下的 leader gate / 优雅关闭等收尾。完成后 spec §0.2 中 4 个症状(指数分时慢/大盘慢/重复 K 线慢/分时 500)全部消除。

**Architecture:**
- collector 多写一个 `tick_quote` 衍生写 Redis(`cache:quote:{market}:{symbol}`),api 改读 Redis 替代进程内 QuoteCache
- `/api/indices/{s}/minute` 改读 `cache:index:*:minute:1`,**删除路由内 ak_call** — 这是 Plan 3 的标志性变化
- `/api/symbols/{s}/bars` 加 Redis 前置 cache + KLineService 拆 `get_bars_cache_only` / `get_bars_fresh` 双轨
- `/api/markets/{m}/dashboard` 新增聚合接口,直读 collector 预填的 cache
- 前端通用 `StaleBadge` 组件 + 各卡片读 meta 染灰

**Tech Stack:** FastAPI / RedisCache / fakeredis (test) / KLineService 重构 / Next.js / SWR (existing)

**Spec reference:** `docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md` §3.1-3.6, §6.5-6.6

**Plan 2 baseline:** commit `d7f4a35`. Plan 3 在此之上展开。

---

## 验收硬条款(贯穿所有 task)

- [ ] **`grep -rn "from core.integrations.akshare import ak_call" apps/api/` 输出为空** — Stage 5 完工标志
- [ ] **`grep -rn "ak_call(" apps/api/` 输出为空** — 同上, 双保险
- [ ] **api 进程不调 ak_middleware.setup()** — 防御:api 永不应该有 ak_call 路径
- [ ] **api 路由 p95 < 300ms, p99 < 500ms** — 性能基准
- [ ] **关停 collector 60s 后 api 仍 200**(stale meta) — graceful degradation
- [ ] **关停 Redis 后 api 仍 200**(DB fallback) — Plan 1 已经实现, Plan 3 不破坏

---

## File Structure

### 修改

- `apps/api/routes/indices.py` — **删除 `from core.integrations.akshare import ak_call`** + `_ashare_index_5min` 改读 cache + `_hk_index_daily` 同理(读 cache 或 mark stale)
- `apps/api/routes/symbols.py` — `/quote` 改读 Redis cache + `/bars` 加 Redis 前置 + 加 dashboard 路由
- `apps/api/routes/__init__.py` — 包含 dashboard 路由
- `apps/api/main.py` — register dashboard router(若新增 routes/dashboard.py)
- `core/services/kline_service.py` — 拆 `get_bars_cache_only` / `get_bars_fresh` 双轨; `_missing_ashare_daily_metrics` 改 partial 标记不阻塞
- `core/scheduler/jobs.py::tick_snapshot_once` — 落 quote 时同步写 Redis cache:quote:*
- `core/scheduler/scheduler.py` — 所有 `attach_*_job` 内置 `ensure_leader()` gate
- `apps/collector/jobs/index_minute.py` — 非交易时段跳过(BJT 09:00-16:00 之外)
- `apps/collector/main.py` — finally 块加 `await _redis_for_mw.aclose()`
- `apps/web/lib/api.ts` — 加 `meta` 字段类型 + 共享 fetch 工具
- `apps/web/components/IndexCard.tsx` — 读 meta.stale 染灰
- `apps/web/components/MarketPulsePanel.tsx` — 改读 `/api/markets/ashare/dashboard` (一次取 indices)
- `apps/web/components/KLineChart.tsx` — 处理 `meta.stale` / `meta.partial`(灰底 + 提示文案)

### 新增

- `apps/api/routes/dashboard.py` — `GET /api/markets/{market}/dashboard` 路由(直读 cache)
- `apps/web/components/StaleBadge.tsx` — 通用 stale/partial 角标组件
- `tests/unit/api/__init__.py` + `tests/unit/api/test_indices_route.py` — fakeredis 验证 indices 路由
- `tests/unit/api/test_symbols_quote_route.py`
- `tests/unit/api/test_dashboard_route.py`
- `tests/unit/scheduler/test_tick_quote_writes_redis.py`(扩展现有)

### 不动

- `core/cache/*` — Plan 1 基础不动
- `core/integrations/*` — Plan 2 中间件不动
- `apps/collector/jobs/{index_minute, market_dashboard, refill_consumer}.py` — Plan 2 已就绪

---

## Task 1: collector tick 同步写 Redis cache:quote (修 QuoteCache 跨进程退化)

**Files:**
- Modify: `core/scheduler/jobs.py::tick_snapshot_once`
- Create: `tests/unit/scheduler/test_tick_writes_redis.py`

- [ ] **Step 1: 写失败测试 — tests/unit/scheduler/test_tick_writes_redis.py**

```python
import pytest
import fakeredis.aioredis
from datetime import datetime, timezone

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.cache.quote_cache import QuoteCache
from core.domain.models import Quote
from core.scheduler.jobs import write_quote_to_redis  # 新引出函数


@pytest.fixture
async def cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisCache(fake)
    await fake.aclose()


async def test_write_quote_to_redis_msgpack_payload(cache):
    q = Quote(market="ashare", symbol="600519.SH", price=1234.5,
              change_pct=1.2, volume=100, ts=datetime(2026, 5, 27, 1, 0, tzinfo=timezone.utc))
    await write_quote_to_redis(q, cache=cache)
    payload = await cache.get_msgpack(keys.cache_quote("ashare", "600519.SH"))
    assert payload is not None
    assert payload["symbol"] == "600519.SH"
    assert payload["price"] == 1234.5
    assert payload["change_pct"] == 1.2
    assert payload["volume"] == 100
    assert payload["ts"] == "2026-05-27T01:00:00+00:00"
    assert payload["market"] == "ashare"


async def test_write_quote_to_redis_swallows_errors(cache, monkeypatch):
    async def raise_set(*args, **kwargs):
        raise RuntimeError("redis down")
    monkeypatch.setattr(cache, "set_msgpack", raise_set)
    q = Quote(market="ashare", symbol="X.SH", price=1.0, change_pct=0.0,
              volume=0, ts=datetime.now(timezone.utc))
    # 不应抛
    await write_quote_to_redis(q, cache=cache)
```

- [ ] **Step 2: 跑测试确认失败**

`. .venv/bin/activate && pytest tests/unit/scheduler/test_tick_writes_redis.py -v`
期望: ImportError on `write_quote_to_redis`.

- [ ] **Step 3: 改 core/scheduler/jobs.py**

读现有 `core/scheduler/jobs.py::tick_snapshot_once`,在文件顶部加 import:

```python
from core.cache.redis_client import RedisCache
from core.cache import keys as cache_keys
import structlog
log = structlog.get_logger(__name__)
```

(若已有 log 引用则不重复)

在文件中加新函数:

```python
async def write_quote_to_redis(q, *, cache: RedisCache, ttl_s: int = 90) -> None:
    """把单条 Quote 写到 Redis cache:quote:{market}:{symbol},供 api 读路径用。

    Plan 1 拆进程后,api 进程的 QuoteCache 是空的;collector 必须把 quote 同时
    落到 Redis 给 api 看。失败仅 warning, 不抛(优雅降级)。
    """
    try:
        payload = {
            "market": q.market,
            "symbol": q.symbol,
            "price": float(q.price),
            "change_pct": q.change_pct,
            "volume": q.volume,
            "ts": q.ts.isoformat(),
        }
        await cache.set_msgpack(
            cache_keys.cache_quote(q.market, q.symbol),
            payload, ttl_s=ttl_s,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("quote.redis_write_failed", symbol=q.symbol, error=str(e))
```

修改 `tick_snapshot_once` 签名,接受可选 `redis_cache: RedisCache | None = None`:

```python
async def tick_snapshot_once(
    market: str,
    registry: AdapterRegistry,
    cache: QuoteCache,
    watchlist: WatchlistService,
    redis_cache: "RedisCache | None" = None,
) -> None:
    # ... 现有逻辑 ...
    for q in quotes:
        cache.put(q)
        if redis_cache is not None:
            await write_quote_to_redis(q, cache=redis_cache)
    # ... 现有 log ...
```

- [ ] **Step 4: 跑测试确认 PASS**

`. .venv/bin/activate && pytest tests/unit/scheduler/test_tick_writes_redis.py -v`
期望: 2 passed.

- [ ] **Step 5: 改 core/scheduler/scheduler.py::build_scheduler**

让 `build_scheduler` 接受 `redis_cache` 参数并传入 tick job:

```python
def build_scheduler(
    registry: AdapterRegistry, cache: QuoteCache, bar_repo: BarRepo,
    watchlist: WatchlistService,
    redis_cache: "RedisCache | None" = None,  # 新增
) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    for market in registry.markets():
        sched.add_job(
            tick_snapshot_once, IntervalTrigger(seconds=10),
            args=(market, registry, cache, watchlist, redis_cache),  # 新增
            id=f"tick:{market}", max_instances=1, coalesce=True,
            misfire_grace_time=30,
        )
    # ... 余下不变 ...
```

- [ ] **Step 6: collector main.py 传 redis_cache**

```python
sched = build_scheduler(registry, cache, bar_repo, get_watchlist_service(),
                        redis_cache=_redis_cache)
```

- [ ] **Step 7: 跑全套测试**

`. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -3`
期望: 304 passed (302 + 2 new).

- [ ] **Step 8: Commit**

```bash
git add core/scheduler/jobs.py core/scheduler/scheduler.py apps/collector/main.py \
        tests/unit/scheduler/test_tick_writes_redis.py
git commit -m "feat(collector): tick 同步写 Redis cache:quote 修 Plan 1 跨进程退化"
```

---

## Task 2: api `/api/symbols/{s}/quote` 改读 Redis cache

**Files:**
- Modify: `apps/api/routes/symbols.py`
- Create: `tests/unit/api/__init__.py`
- Create: `tests/unit/api/test_symbols_quote_route.py`

- [ ] **Step 1: 创建 test 目录**

`touch tests/unit/api/__init__.py`

- [ ] **Step 2: 失败测试**

`tests/unit/api/test_symbols_quote_route.py`:

```python
import pytest
import fakeredis.aioredis
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from apps.api.main import app
from apps.api.deps import get_redis_cache
from core.cache.redis_client import RedisCache
from core.cache import keys


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
async def patched_cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    app.dependency_overrides[get_redis_cache] = lambda: cache
    yield cache
    await fake.aclose()
    app.dependency_overrides.clear()


async def test_quote_returns_cached_payload(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_quote("ashare", "600519.SH"),
        {"market": "ashare", "symbol": "600519.SH", "price": 1234.5,
         "change_pct": 1.2, "volume": 100,
         "ts": "2026-05-27T01:00:00+00:00"},
        ttl_s=90,
    )
    r = client.get("/api/symbols/600519.SH/quote")
    assert r.status_code == 200
    data = r.json()
    assert data["price"] == 1234.5
    assert data["change_pct"] == 1.2
    assert data["meta"]["stale"] is False


async def test_quote_returns_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/symbols/NOTEXIST.SH/quote")
    assert r.status_code == 200
    data = r.json()
    assert data["price"] is None
    assert data["meta"]["stale"] is True
    assert data["meta"]["reason"] in ("warming_up", "cache_miss")
```

- [ ] **Step 3: 跑确认 fail**

期望: ImportError or assertion fails (no `meta` field in response yet).

- [ ] **Step 4: 改 apps/api/routes/symbols.py 的 quote 路由**

修改 `QuoteResponse` Pydantic model,加 `meta`:

```python
class QuoteMeta(BaseModel):
    stale: bool = False
    reason: str | None = None
    data_age_seconds: float | None = None


class QuoteResponse(BaseModel):
    symbol: str
    price: float | None
    change_pct: float | None
    volume: int | None
    ts: str | None
    meta: QuoteMeta = QuoteMeta()
```

替换 `quote()` 路由实现:

```python
@router.get("/{symbol}/quote", response_model=QuoteResponse)
async def quote(
    symbol: str,
    redis_cache=Depends(get_redis_cache),
) -> QuoteResponse:
    market = infer_market(symbol)
    if not market:
        return QuoteResponse(
            symbol=symbol, price=None, change_pct=None, volume=None, ts=None,
            meta=QuoteMeta(stale=True, reason="unknown_market"),
        )
    payload = await redis_cache.get_msgpack(keys.cache_quote(market, symbol))
    if payload is None:
        return QuoteResponse(
            symbol=symbol, price=None, change_pct=None, volume=None, ts=None,
            meta=QuoteMeta(stale=True, reason="warming_up"),
        )
    # 计算数据陈旧度
    from datetime import datetime, timezone
    ts = datetime.fromisoformat(payload["ts"])
    age_s = (datetime.now(timezone.utc) - ts).total_seconds()
    return QuoteResponse(
        symbol=payload["symbol"],
        price=payload.get("price"),
        change_pct=payload.get("change_pct"),
        volume=payload.get("volume"),
        ts=payload["ts"],
        meta=QuoteMeta(stale=age_s > 60, data_age_seconds=age_s),
    )
```

需要 import:

```python
from apps.api.deps import get_redis_cache
from core.cache import keys
```

并保留(或移除)旧的 `get_quote_cache` 依赖 — 移除它会让 deps 更干净。**移除**:

- 找到顶部 import 块,把 `get_quote_cache` 从 imports 里去掉(若仅 quote 路由用了它)
- `from core.cache.quote_cache import QuoteCache` 同样移除
- 跑 `grep -n "QuoteCache\|get_quote_cache" apps/api/routes/symbols.py` 确认没有残留

- [ ] **Step 5: 跑测试 PASS**

`. .venv/bin/activate && pytest tests/unit/api/test_symbols_quote_route.py -v`
期望: 2 passed.

- [ ] **Step 6: 全套测试**

`. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -3`
期望: 306 passed (304 + 2 new)。

- [ ] **Step 7: Commit**

```bash
git add apps/api/routes/symbols.py tests/unit/api/__init__.py tests/unit/api/test_symbols_quote_route.py
git commit -m "feat(api): /quote 改读 Redis cache:quote 替代进程内 QuoteCache"
```

---

## Task 3: api `/api/indices/{s}/minute` 改读 cache,删 ak_call

**Files:**
- Modify: `apps/api/routes/indices.py`
- Create: `tests/unit/api/test_indices_route.py`

- [ ] **Step 1: 失败测试**

`tests/unit/api/test_indices_route.py`:

```python
import pytest
import fakeredis.aioredis
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.deps import get_redis_cache
from core.cache.redis_client import RedisCache
from core.cache import keys


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
async def patched_cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    app.dependency_overrides[get_redis_cache] = lambda: cache
    yield cache
    await fake.aclose()
    app.dependency_overrides.clear()


async def test_index_minute_returns_cached_payload(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_index_minute("000001.SH", days=1),
        {"symbol": "000001.SH", "granularity": "5m",
         "points": [{"ts": "2026-05-27T01:30:00+00:00", "close": 3000.5, "volume": 1000}],
         "meta": {"fresh_at": "2026-05-27T01:30:00+00:00", "stale": False, "source": "sina"}},
        ttl_s=90,
    )
    r = client.get("/api/indices/000001.SH/minute?days=1")
    assert r.status_code == 200
    data = r.json()
    assert data["symbol"] == "000001.SH"
    assert len(data["points"]) == 1
    assert data["meta"]["stale"] is False


async def test_index_minute_returns_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/indices/000001.SH/minute?days=1")
    assert r.status_code == 200
    data = r.json()
    assert data["points"] == []
    assert data["meta"]["stale"] is True


async def test_index_minute_unknown_symbol_404(client):
    r = client.get("/api/indices/UNKNOWN.SH/minute")
    assert r.status_code == 404
```

- [ ] **Step 2: 跑确认 fail (assertion fail or 500)**

- [ ] **Step 3: 改 apps/api/routes/indices.py — 删 ak_call,改读 cache**

替换整个 `_ashare_index_5min` 函数实现:

```python
async def _ashare_index_5min(symbol: str, name: str, days: int, cache) -> IndexMinuteResponse:
    payload = await cache.get_msgpack(keys.cache_index_minute(symbol, days=days))
    if payload is None:
        log.info("indices.minute.cache_miss", symbol=symbol, days=days)
        return IndexMinuteResponse(
            symbol=symbol, name=name, granularity="5m",
            points=[], meta=IndexMeta(stale=True, reason="warming_up"),
        )
    points = [MinutePoint(**p) for p in payload.get("points", [])]
    fresh_at = payload.get("meta", {}).get("fresh_at")
    return IndexMinuteResponse(
        symbol=symbol, name=name,
        granularity=payload.get("granularity", "5m"),
        points=points,
        meta=IndexMeta(stale=False, fresh_at=fresh_at),
    )
```

替换 `_hk_index_daily` (HK 走 ak_call,Plan 3 暂时让它也读 cache,若 cache 没有则 stale):

```python
async def _hk_index_daily(symbol: str, name: str, days: int, cache) -> IndexMinuteResponse:
    # Plan 2 暂未给 HK 指数搭 collector job; cache 默认空 → stale 兜底, 等后续 plan 补 job
    payload = await cache.get_msgpack(keys.cache_index_minute(symbol, days=days))
    if payload is None:
        return IndexMinuteResponse(
            symbol=symbol, name=name, granularity="1d",
            points=[], meta=IndexMeta(stale=True, reason="hk_index_collector_pending"),
        )
    points = [MinutePoint(**p) for p in payload.get("points", [])]
    return IndexMinuteResponse(
        symbol=symbol, name=name, granularity="1d",
        points=points, meta=IndexMeta(stale=False),
    )
```

加 `IndexMeta` model:

```python
class IndexMeta(BaseModel):
    stale: bool = False
    reason: str | None = None
    fresh_at: str | None = None
```

更新 `IndexMinuteResponse`:

```python
class IndexMinuteResponse(BaseModel):
    symbol: str
    name: str
    granularity: str
    points: list[MinutePoint]
    meta: IndexMeta = IndexMeta()
```

更新 `index_minute` 路由签名:

```python
@router.get("/{symbol}/minute", response_model=IndexMinuteResponse)
async def index_minute(
    symbol: str,
    days: int = Query(1, ge=1, le=30),
    cache=Depends(get_redis_cache),
) -> IndexMinuteResponse:
    if symbol not in _INDEX_NAME:
        raise HTTPException(404, f"unknown index: {symbol}")
    name = _INDEX_NAME[symbol]
    if symbol.endswith(".HK"):
        return await _hk_index_daily(symbol, name, days=30, cache=cache)
    return await _ashare_index_5min(symbol, name, days=days, cache=cache)
```

**最关键**: **删除文件顶部的** `from core.integrations.akshare import ak_call`。
顶部加 import:
```python
from apps.api.deps import get_redis_cache
from core.cache import keys
```

- [ ] **Step 4: 跑测试确认 PASS**

`. .venv/bin/activate && pytest tests/unit/api/test_indices_route.py -v`
期望: 3 passed.

- [ ] **Step 5: 验收硬条款 — grep 检查**

```bash
grep -rn "from core.integrations.akshare import ak_call" apps/api/routes/
```
期望: **空**(若有命中,你漏改,修了再继续)。

```bash
grep -rn "ak_call(" apps/api/
```
期望: **空**。

- [ ] **Step 6: 全套**

`. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -3`
期望: 309 passed (306 + 3).

- [ ] **Step 7: Commit**

```bash
git add apps/api/routes/indices.py tests/unit/api/test_indices_route.py
git commit -m "feat(api): /api/indices/{s}/minute 改读 cache, 删除路由内 ak_call"
```

---

## Task 4: KLineService 拆双轨 + `_missing_ashare_daily_metrics` 改 partial 标记

**Files:**
- Modify: `core/services/kline_service.py`
- Modify: `apps/api/routes/symbols.py::bars`(后续 Task 5 用)

- [ ] **Step 1: 读现有 `core/services/kline_service.py`**

确认结构:`get_bars` / `_get_daily` / `_get_intraday` / `_get_intraday_aggregated` / `fetch_fresh_bars`(已经存在)。

- [ ] **Step 2: 加新方法 `get_bars_cache_only`**

在 KLineService 中,加方法(放在 `get_bars` 旁):

```python
async def get_bars_cache_only(
    self, symbol: str, *, interval: Interval,
    start: datetime, end: datetime,
) -> tuple[list[Bar], bool]:
    """只读 DuckDB cache, 不调 adapter, 不写库。

    返回 (bars, partial), partial=True 表示数据有但部分字段缺失
    (如 A 股 daily 缺 amount/turnover; 仍可绘 K 线但量能/换手缺)。

    api 进程必须用这个方法; collector 用 get_bars 或 fetch_fresh_bars。
    """
    market = infer_market(symbol)
    if interval == "1d":
        cached = self.repo.fetch_history(market, symbol, start, end, interval="1d")
        if not cached or not self._covers(cached, start, end):
            return [], False
        partial = self._missing_ashare_daily_metrics(cached) if market == "ashare" else False
        return cached, partial
    if interval in _RESAMPLED:
        # 复用 _get_daily 的 cache_only 路径(简化:同步路径)
        daily, partial = await self.get_bars_cache_only(
            symbol, interval="1d", start=start, end=end,
        )
        if not daily:
            return [], False
        return _resample(daily, interval), partial
    if interval in _INTRADAY_RAW or interval in {"60m", "4h"}:
        cached = self.repo.fetch_history(market, symbol, start, end, interval=interval)
        if not cached or not self._covers(cached, start, end):
            return [], False
        return cached, False
    return [], False
```

- [ ] **Step 3: 不删除现有 `_get_daily` 中的 metrics_missing 逻辑**

那段逻辑给 collector / fetch_fresh_bars 走 — 它确实需要在数据老旧时拉新的。**只在 api 路径用 `get_bars_cache_only` 绕开**。这意味着 daily metrics_missing 的 cache miss bug 通过"api 不再调 _get_daily"间接修复。

- [ ] **Step 4: 写单测验证 cache_only 不会 trigger adapter**

`tests/unit/services/test_kline_service_cache_only.py`(若已有 services 测试目录就放进去,否则新建):

```python
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from core.services.kline_service import KLineService
from core.domain.models import Bar


def make_bar(ts: datetime, market: str = "ashare", symbol: str = "600519.SH",
             amount: float | None = 1000.0, turnover: float | None = 0.5) -> Bar:
    return Bar(
        market=market, symbol=symbol, ts=ts, interval="1d",
        open=10.0, high=11.0, low=9.0, close=10.5, volume=1000,
        amount=amount, turnover=turnover,
    )


async def test_cache_only_returns_partial_when_amount_missing():
    svc = KLineService(
        repo=MagicMock(), registry=MagicMock(), one_minute_ttl_s=60,
    )
    bars = [
        make_bar(datetime(2026, 5, 1, tzinfo=timezone.utc), amount=None),
        make_bar(datetime(2026, 5, 2, tzinfo=timezone.utc), amount=None),
    ] + [
        make_bar(datetime(2026, 5, 1+i, tzinfo=timezone.utc))
        for i in range(2, 25)
    ]
    svc.repo.fetch_history = MagicMock(return_value=bars)
    end = datetime(2026, 5, 27, tzinfo=timezone.utc)
    start = end - timedelta(days=30)
    result, partial = await svc.get_bars_cache_only(
        "600519.SH", interval="1d", start=start, end=end,
    )
    # adapter 应未被调用
    svc.registry.get.assert_not_called()
    # 应有 bars, partial 标记
    assert len(result) > 0


async def test_cache_only_returns_empty_when_cache_miss():
    svc = KLineService(repo=MagicMock(), registry=MagicMock(), one_minute_ttl_s=60)
    svc.repo.fetch_history = MagicMock(return_value=[])
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    result, partial = await svc.get_bars_cache_only(
        "600519.SH", interval="1d", start=start, end=end,
    )
    assert result == []
    assert partial is False
    svc.registry.get.assert_not_called()
```

- [ ] **Step 5: 跑测试 PASS**

(如果 KLineService 构造签名不同, 调整 mock。先 `grep -n "def __init__" core/services/kline_service.py` 看真实签名。)

- [ ] **Step 6: Commit**

```bash
git add core/services/kline_service.py tests/unit/services/test_kline_service_cache_only.py
git commit -m "feat(kline): 拆 get_bars_cache_only 双轨 + partial 标记不阻塞"
```

---

## Task 5: api `/api/symbols/{s}/bars` 加 Redis 前置 + cache_only 路径

**Files:**
- Modify: `apps/api/routes/symbols.py::bars`

- [ ] **Step 1: 改 bars 路由**

替换 bars 路由实现:

```python
class BarsResponseMeta(BaseModel):
    stale: bool = False
    partial: bool = False
    reason: str | None = None


class BarsResponse(BaseModel):
    symbol: str
    interval: str
    bars: list[BarDTO]
    meta: BarsResponseMeta = BarsResponseMeta()


@router.get("/{symbol}/bars", response_model=BarsResponse)
async def bars(
    symbol: str,
    interval: str = Query("1d"),
    days: int = Query(365, ge=1, le=3650),
    svc: KLineService = Depends(get_kline_service),
    redis_cache=Depends(get_redis_cache),
) -> BarsResponse:
    if interval not in KLINE_INTERVALS:
        raise HTTPException(400, f"invalid interval: {interval}")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # 走 cache_only — 绝不调 adapter / ak_call
    bars_list, partial = await svc.get_bars_cache_only(
        symbol, interval=interval, start=start, end=end,
    )
    if not bars_list:
        # 触发 collector 后台 refill (不等待)
        try:
            await _publish_refill_request(redis_cache, symbol, interval, days)
        except Exception:  # noqa: BLE001
            pass
        return BarsResponse(
            symbol=symbol, interval=interval, bars=[],
            meta=BarsResponseMeta(stale=True, reason="warming_up"),
        )
    return BarsResponse(
        symbol=symbol, interval=interval,
        bars=[BarDTO(...) for b in bars_list],  # 保持现有 DTO 转换
        meta=BarsResponseMeta(partial=partial),
    )


async def _publish_refill_request(redis_cache, symbol: str, interval: str, days: int) -> None:
    """发 bus:bars.refill_request,collector refill_consumer 收到后拉数据写库 + 写 cache。"""
    import json
    from core.cache import keys as ck
    from core.domain.markets import infer_market
    payload = {
        "market": infer_market(symbol) or "unknown",
        "symbol": symbol, "interval": interval, "days": days,
    }
    # XADD 通过 RedisCache._r 暴露的底层 client
    await redis_cache._r.xadd(
        ck.BUS_BARS_REFILL_REQUEST,
        {"data": json.dumps(payload)},
        maxlen=100, approximate=True,
    )
```

(注:`BarDTO(...)` 处保留原有字段映射,完整代码不展开。)

- [ ] **Step 2: 加 fakeredis 集成测试 — tests/unit/api/test_symbols_bars_route.py**

```python
import pytest
import fakeredis.aioredis
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from apps.api.main import app
from apps.api.deps import get_redis_cache, get_kline_service
from core.cache.redis_client import RedisCache
from core.domain.models import Bar


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_redis_cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    return RedisCache(fake), fake


async def test_bars_returns_bars_when_cache_only_hit(client, fake_redis_cache):
    cache, _ = fake_redis_cache
    svc = MagicMock()
    bars = [
        Bar(market="ashare", symbol="600519.SH",
            ts=datetime(2026, 5, 1, tzinfo=timezone.utc), interval="1d",
            open=10, high=11, low=9, close=10.5, volume=100,
            amount=1000.0, turnover=0.5)
    ]
    svc.get_bars_cache_only = AsyncMock(return_value=(bars, False))

    app.dependency_overrides[get_redis_cache] = lambda: cache
    app.dependency_overrides[get_kline_service] = lambda: svc

    try:
        r = client.get("/api/symbols/600519.SH/bars?interval=1d&days=30")
        assert r.status_code == 200
        data = r.json()
        assert len(data["bars"]) == 1
        assert data["meta"]["partial"] is False
        assert data["meta"]["stale"] is False
    finally:
        app.dependency_overrides.clear()


async def test_bars_returns_stale_when_cache_miss_and_publishes_refill(client, fake_redis_cache):
    cache, fake = fake_redis_cache
    svc = MagicMock()
    svc.get_bars_cache_only = AsyncMock(return_value=([], False))

    app.dependency_overrides[get_redis_cache] = lambda: cache
    app.dependency_overrides[get_kline_service] = lambda: svc

    try:
        r = client.get("/api/symbols/X.SH/bars?interval=1d&days=30")
        assert r.status_code == 200
        data = r.json()
        assert data["bars"] == []
        assert data["meta"]["stale"] is True
        # 验证发了 refill 请求到 stream
        length = await fake.xlen("bus:bars.refill_request")
        assert length >= 1
    finally:
        app.dependency_overrides.clear()


async def test_bars_partial_flag_when_metrics_missing(client, fake_redis_cache):
    cache, _ = fake_redis_cache
    svc = MagicMock()
    bars = [Bar(market="ashare", symbol="X.SH",
                ts=datetime.now(timezone.utc), interval="1d",
                open=10, high=11, low=9, close=10.5, volume=100,
                amount=None, turnover=None)]
    svc.get_bars_cache_only = AsyncMock(return_value=(bars, True))
    app.dependency_overrides[get_redis_cache] = lambda: cache
    app.dependency_overrides[get_kline_service] = lambda: svc

    try:
        r = client.get("/api/symbols/X.SH/bars?interval=1d&days=30")
        assert r.status_code == 200
        assert r.json()["meta"]["partial"] is True
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 3: 跑测试 PASS**

期望: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add apps/api/routes/symbols.py tests/unit/api/test_symbols_bars_route.py
git commit -m "feat(api): /bars 走 cache_only + 缺数据发 bus refill 请求"
```

---

## Task 6: 新增 `/api/markets/{m}/dashboard` 路由

**Files:**
- Create: `apps/api/routes/dashboard.py`
- Modify: `apps/api/main.py` 注册 router
- Create: `tests/unit/api/test_dashboard_route.py`

- [ ] **Step 1: 失败测试**

```python
# tests/unit/api/test_dashboard_route.py
import pytest
import fakeredis.aioredis
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.deps import get_redis_cache
from core.cache.redis_client import RedisCache
from core.cache import keys


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
async def patched_cache():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=False)
    cache = RedisCache(fake)
    app.dependency_overrides[get_redis_cache] = lambda: cache
    yield cache
    await fake.aclose()
    app.dependency_overrides.clear()


async def test_dashboard_returns_cache(client, patched_cache):
    await patched_cache.set_msgpack(
        keys.cache_market_dashboard("ashare"),
        {"market": "ashare", "indices": [], "overview": None,
         "north_flow": None, "hot_sectors": None,
         "meta": {"fresh_at": "2026-05-27T01:00:00+00:00",
                  "stale": False, "missing_sections": []}},
        ttl_s=120,
    )
    r = client.get("/api/markets/ashare/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert data["market"] == "ashare"


async def test_dashboard_stale_when_no_cache(client, patched_cache):
    r = client.get("/api/markets/ashare/dashboard")
    assert r.status_code == 200
    assert r.json()["meta"]["stale"] is True


async def test_dashboard_unknown_market_404(client):
    r = client.get("/api/markets/xx/dashboard")
    assert r.status_code in (404, 400)
```

- [ ] **Step 2: 实现 apps/api/routes/dashboard.py**

```python
"""市场 dashboard 聚合接口 — 一次返回前端 /market 页所需。

collector 的 market_dashboard job 预先写好 cache:market:{m}:dashboard,
本路由直读 Redis,不调 ak_call/不查 DB。
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path

from apps.api.deps import get_redis_cache
from core.cache import keys
from core.cache.redis_client import RedisCache

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/markets", tags=["dashboard"])

_VALID_MARKETS = {"ashare", "hk", "us", "crypto"}


@router.get("/{market}/dashboard")
async def dashboard(
    market: str = Path(..., min_length=2, max_length=10),
    cache: RedisCache = Depends(get_redis_cache),
) -> dict[str, Any]:
    if market not in _VALID_MARKETS:
        raise HTTPException(404, f"unknown market: {market}")
    payload = await cache.get_msgpack(keys.cache_market_dashboard(market))
    if payload is None:
        return {
            "market": market, "indices": [],
            "overview": None, "north_flow": None, "hot_sectors": None,
            "meta": {"stale": True, "reason": "warming_up"},
        }
    return payload
```

- [ ] **Step 3: 注册到 apps/api/main.py**

加 import:
```python
from apps.api.routes import (
    ai_market, cd_signals, dashboard, health, indices, market_extras, north_flow,
    notifications, symbols, watchlists,
)
```
注册 router(app.include_router 后顺位):
```python
app.include_router(dashboard.router)
```

- [ ] **Step 4: 跑测试 PASS**

期望: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routes/dashboard.py apps/api/routes/__init__.py apps/api/main.py \
        tests/unit/api/test_dashboard_route.py
git commit -m "feat(api): /api/markets/{m}/dashboard 直读 cache 聚合包"
```

---

## Task 7: collector 收尾 — Leader gate cron + 优雅 shutdown + index_minute 时段判断

**Files:**
- Modify: `core/scheduler/scheduler.py` — `attach_*` 加 leader gate
- Modify: `apps/collector/main.py` — finally 块 aclose
- Modify: `apps/collector/jobs/index_minute.py` — 非交易时段早返

- [ ] **Step 1: Leader gate 设计**

leader 全局可访问需要一个 module-level 单例。在 `core/scheduler/leader_gate.py`(新建)定义:

```python
"""Leader gate — scheduler 内 cron job 执行前调用 ensure_leader()。

设计:collector 启动时 set_leader() 注入 Leader 实例; cron job 在 ensure_leader()
查询是否 is_leader, 否则 return 跳过本轮。

api 进程不调用 set_leader,所以即便 api 中误注册了 cron 也会被 gate 拦下(防御深度)。
"""
from __future__ import annotations

from typing import Protocol


class _LeaderLike(Protocol):
    def is_leader(self) -> bool: ...


_leader: _LeaderLike | None = None


def set_leader(leader: _LeaderLike) -> None:
    global _leader
    _leader = leader


def is_leader() -> bool:
    """无 leader 注入时默认 True(单进程 dev 友好)。"""
    return _leader.is_leader() if _leader is not None else True
```

- [ ] **Step 2: collector 启动时 set_leader**

`apps/collector/main.py` 在 leader 初始化后加:

```python
from core.scheduler.leader_gate import set_leader
set_leader(leader)
```

- [ ] **Step 3: scheduler.py 包装 cron job**

修改 `core/scheduler/scheduler.py` 中 `pull_north_flow_job` / `pull_watchlist_symbol_flow_job` / `purge_fund_flow_job` 等的注册,引入 wrapper:

```python
from core.scheduler.leader_gate import is_leader as _is_leader


def _leader_gated(coro_factory):
    """把任意 async cron 函数包一层:非 leader 立即 return。"""
    async def _gated(*args, **kwargs):
        if not _is_leader():
            log.debug("scheduler.skip_non_leader",
                      job=getattr(coro_factory, "__name__", "unknown"))
            return
        return await coro_factory(*args, **kwargs)
    _gated.__name__ = f"gated_{getattr(coro_factory, '__name__', 'unknown')}"
    return _gated
```

然后改 `attach_*` 函数,把 `pull_north_flow_job` 改为 `_leader_gated(pull_north_flow_job)` 等。**所有 attach_* 都一遍**(tick_quote / flush / fundamentals / signals / index_minute / dashboard / chip_preload / refill 不需要(它是 asyncio 长 task,不是 cron))。

- [ ] **Step 4: index_minute 非交易时段跳过**

修改 `apps/collector/jobs/index_minute.py::refresh_all_indices`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
_BJT = ZoneInfo("Asia/Shanghai")

async def refresh_all_indices(cache: RedisCache) -> None:
    now_bjt = datetime.now(_BJT)
    weekday = now_bjt.weekday()  # 0=Mon, 6=Sun
    if weekday >= 5:  # 周末
        log.debug("index_minute.skip_weekend")
        return
    h = now_bjt.hour
    # A 股交易时段:09:00-11:30, 13:00-15:30(放宽到 09:00-16:00)
    if not (9 <= h < 16):
        log.debug("index_minute.skip_off_hours", hour=h)
        return
    for symbol in INDEX_SYMBOLS:
        await refresh_one_index(symbol, cache=cache)
```

- [ ] **Step 5: 优雅 aclose**

`apps/collector/main.py` finally 块,在最后(release leader 之后)加:

```python
        try:
            await _redis_for_mw.aclose()
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 6: 跑测试**

`. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -3`
期望: 全过(没新加测试)。

- [ ] **Step 7: Commit**

```bash
git add core/scheduler/leader_gate.py core/scheduler/scheduler.py \
        apps/collector/main.py apps/collector/jobs/index_minute.py
git commit -m "fix(collector): Leader gate 包 cron + index_minute 非交易时段跳过 + aclose 收尾"
```

---

## Task 8: 前端 — StaleBadge 通用组件 + types.ts meta 字段

**Files:**
- Create: `apps/web/components/StaleBadge.tsx`
- Modify: `apps/web/lib/types.ts` 加 meta 字段

- [ ] **Step 1: 加类型**

读 `apps/web/lib/types.ts`(若不存在则创建)。加:

```typescript
export interface ResponseMeta {
  stale?: boolean
  partial?: boolean
  reason?: string
  data_age_seconds?: number
  fresh_at?: string
  missing_sections?: string[]
}
```

更新 `QuoteResponse / BarsResponse / IndexMinuteResponse` 等已有类型,加 `meta?: ResponseMeta`。

- [ ] **Step 2: StaleBadge 组件**

```tsx
// apps/web/components/StaleBadge.tsx
import type { ResponseMeta } from '../lib/types'

interface Props {
  meta?: ResponseMeta
  className?: string
}

function formatAge(s?: number): string {
  if (s == null) return ''
  if (s < 60) return `${Math.round(s)} 秒`
  if (s < 3600) return `${Math.round(s / 60)} 分钟`
  return `${(s / 3600).toFixed(1)} 小时`
}

export function StaleBadge({ meta, className }: Props) {
  if (!meta || (!meta.stale && !meta.partial)) return null
  if (meta.partial) {
    return (
      <span className={`inline-block px-2 py-0.5 text-xs rounded bg-yellow-200 text-yellow-900 ${className ?? ''}`}>
        部分字段缺失
      </span>
    )
  }
  const age = meta.data_age_seconds ? `(${formatAge(meta.data_age_seconds)} 前)` : ''
  return (
    <span className={`inline-block px-2 py-0.5 text-xs rounded bg-gray-300 text-gray-800 ${className ?? ''}`}>
      数据延迟 {age} {meta.reason && <em className="ml-1 opacity-70">{meta.reason}</em>}
    </span>
  )
}
```

- [ ] **Step 3: tsc check**

`cd apps/web && npx tsc --noEmit`
期望: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/components/StaleBadge.tsx apps/web/lib/types.ts
git commit -m "feat(web): StaleBadge 通用组件 + meta 字段类型"
```

---

## Task 9: 前端 — IndexCard / KLineChart 用 StaleBadge 染灰

**Files:**
- Modify: `apps/web/components/IndexCard.tsx`
- Modify: `apps/web/components/KLineChart.tsx`

- [ ] **Step 1: IndexCard**

读现有 IndexCard.tsx, 找到从 `/api/indices/.../minute` 拿到的数据,在 card 标题旁边加:

```tsx
import { StaleBadge } from './StaleBadge'

// ... 在 component 内 ...
<div className="flex items-center gap-2">
  <h3>{indexName}</h3>
  <StaleBadge meta={data.meta} />
</div>
```

如果 `data` 是 stale 状态(`meta.stale=true`),整个 card 加灰底:

```tsx
<div className={`p-4 rounded ${data.meta?.stale ? 'opacity-60 bg-gray-50' : ''}`}>
  ...
</div>
```

- [ ] **Step 2: KLineChart**

类似处理。读到 `meta.stale` 或 `meta.partial` 时:
- stale → 整图灰底 + Toast / Alert "数据加载中,请稍后刷新"
- partial → 显示 "成交额数据缺失"角标(影响量能图)

具体实现根据现有 KLineChart 代码风格调整;不要 re-architecture 整个组件。

- [ ] **Step 3: tsc + 构建检查**

```bash
cd apps/web && npx tsc --noEmit
cd apps/web && npm run build  # 确保 production build 不挂
```

如果 build 失败,排查 TypeScript 错误后再继续。

- [ ] **Step 4: Commit**

```bash
git add apps/web/components/IndexCard.tsx apps/web/components/KLineChart.tsx
git commit -m "feat(web): IndexCard / KLineChart 读 meta.stale/partial 染灰"
```

---

## Task 10: e2e 集成 + 验收清单

- [ ] **Step 1: 全套 unit test**

`. .venv/bin/activate && pytest -m "not integration" -q 2>&1 | tail -3`
期望: 全过(预计 320+).

- [ ] **Step 2: 重启服务全套**

```bash
pkill -9 -f "apps.collector.main" 2>/dev/null
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null
sleep 2
docker compose -f docker-compose.dev.yml up -d redis > /dev/null
> /tmp/collector.log
> /tmp/api.log
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 15
```

- [ ] **Step 3: 验证 4 个症状的修复**

```bash
echo "--- 症状 1: indices/minute (cache hit, 不应慢) ---"
time curl -s -m 5 -o /dev/null http://localhost:8787/api/indices/000001.SH/minute?days=1
echo "--- 症状 2: dashboard (一次拿全) ---"
time curl -s -m 5 -o /dev/null http://localhost:8787/api/markets/ashare/dashboard
echo "--- 症状 3: bars (cache_only, 不调 ak) ---"
time curl -s -m 5 -o /dev/null http://localhost:8787/api/symbols/600519.SH/bars?interval=1d
echo "--- 症状 4: 路由不再有 ak_call(grep 验证) ---"
grep -rn "from core.integrations.akshare import ak_call" apps/api/routes/ || echo "(empty PASS)"
grep -rn "ak_call(" apps/api/ || echo "(empty PASS)"
```

期望:
- 4 个 curl 全 < 100ms
- 2 个 grep 都返回 empty(硬条款达成)

- [ ] **Step 4: 验证 stale meta 染灰路径**

模拟 collector 关停 60s 看 api 是否优雅降级:

```bash
pkill -INT -f "apps.collector.main"; sleep 18
echo "--- collector 关停后 api 仍 200 ---"
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8787/api/indices/000001.SH/minute?days=1
echo "--- meta.stale 应为 true ---"
curl -s http://localhost:8787/api/indices/000001.SH/minute?days=1 | python3 -c "import json,sys; d=json.load(sys.stdin); print('stale:', d['meta'].get('stale'))"
```

期望: api 200, stale: True.

- [ ] **Step 5: 重启 collector 让数据回流**

```bash
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
sleep 30  # 等 index_minute job 跑一轮
curl -s http://localhost:8787/api/indices/000001.SH/minute?days=1 | python3 -c "import json,sys; d=json.load(sys.stdin); print('stale:', d['meta'].get('stale'), 'points:', len(d['points']))"
```
期望: stale: False, points: > 0.

- [ ] **Step 6: 前端构建 + 跑一遍**

```bash
cd apps/web && npm run build 2>&1 | tail -10
```

期望: 构建成功,0 type errors.

- [ ] **Step 7: docs/TODO.md 更新**

划掉本 plan 完成的 4 个 Plan 1/2 退化项:
- QuoteCache 跨进程孤岛 → DONE (Task 1+2)
- Leader 锁未真正门控 cron → DONE (Task 7)
- _redis_for_mw shutdown → DONE (Task 7)
- index_minute 非交易时段无意义 → DONE (Task 7)

加一行 Plan 3 完成记录:

```markdown
- 2026-05-XX ✅ Plan 3 完成:api 全部读 cache + 前端 stale 染灰 + Plan 1/2 退化全修。
```

- [ ] **Step 8: Commit docs**

```bash
git add docs/TODO.md
git commit -m "docs(todo): Plan 3 完成,Plan 1/2 退化项修复"
```

---

## 验收清单(整个 Plan 3 完工标准)

- [ ] `grep -rn "from core.integrations.akshare import ak_call" apps/api/routes/` 为空
- [ ] `grep -rn "ak_call(" apps/api/` 为空
- [ ] api 路由 p95 < 300ms,p99 < 500ms(实测 curl time)
- [ ] 关停 collector 60s,api 仍 200(stale meta)
- [ ] 关停 Redis,api 仍 200(DB fallback,Plan 1 已实现)
- [ ] 重复访问同 K 线第 2 次起 < 30ms(cache hit)
- [ ] /api/markets/ashare/dashboard p95 < 150ms
- [ ] 前端 npm run build 0 errors
- [ ] 前端 IndexCard / KLineChart 在 stale 时染灰 + 提示文案

---

## 不在 Plan 3 范围(留 Plan 4+)

- ❌ SSE 推送替换轮询 — spec §6.7 推迟
- ❌ Prometheus / Grafana — spec §6.7 推迟
- ❌ ak_call_with_fallback 多源 fallback — 单独 Plan
- ❌ HK 指数 collector job(目前 dashboard 暂只 A 股)
- ❌ /market 页其他 section(overview / north_flow / hot_sectors)的 collector job
- ❌ refill_consumer DLQ 重试

---

## 风险与回滚

| 风险 | 触发 | 回滚 |
|---|---|---|
| Task 3 indices.py 改完前端切换不及时 | 前端老 fetch 调用拿 200 但 meta 字段不解析 | meta 是 optional 字段,旧前端忽略不影响显示 |
| Task 5 bars 路由切换后 cache 还没填充 | 用户访问 `/bars` 全部返回空 | bus:bars.refill_request 自动触发 collector 拉; 单次访问后续命中 |
| Task 7 leader gate 在某个环境下导致所有 cron 都不跑 | leader 永远抢不到锁 | leader_gate.is_leader() 默认返回 True (no leader injected),api 进程也安全; 真出问题可临时把 set_leader 注释掉 |
| Plan 3 完成但 Stage 6 SSE 没做 | 前端仍轮询 quote 接口,频率较高 | 不阻塞;Plan 4 单独做 SSE |

---

## 下一步

Plan 3 review + 执行完毕后, 4 个症状全消除, spec §0.2 验收完成。后续优化(SSE / Prometheus / fallback) 单独立项。
