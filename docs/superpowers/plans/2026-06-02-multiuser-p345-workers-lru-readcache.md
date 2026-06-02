# 多用户化 ③多worker/⑤美股LRU-30/④读缓存 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development。

**Goal:** 收尾多用户化:③ Redis 池上限(多 worker 安全)、⑤ 美股 trades 纯 LRU-30(按最近访问轮换 + 前端"超出实时名额"提示)、④ 历史分页 Redis 页缓存(卸载 collector)。

**Spec:** `docs/superpowers/specs/2026-06-02-multiuser-scaling-design.md` §3/§5/§6。

> 说明:③ 的多 worker/SSE per-worker 已在包② 落地、生产启动(--workers / next build)与 nginx 已在包① `deploy/` 文档给出。本计划 ③ 仅补 Redis 池上限(代码)。

---

## ③ Redis 连接池上限

**Files:** Modify `core/cache/redis_client.py::make_redis`

- [ ] **Step 1: 实现** — `make_redis` 的 `AsyncRedis.from_url(...)` 加 `max_connections`(env 可调,默认 50):
```python
def make_redis(url: str = "redis://127.0.0.1:6379/0") -> AsyncRedis:
    import os
    return AsyncRedis.from_url(
        url, decode_responses=False,
        socket_timeout=None,
        socket_connect_timeout=5,
        health_check_interval=30,
        max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "50")),
    )
```
(保留原 docstring。)

- [ ] **Step 2: 验证** — `python -c "from core.cache.redis_client import make_redis; r=make_redis(); print('ok', r.connection_pool.max_connections)"`(打印 50)

- [ ] **Step 3: 提交**
```bash
git add core/cache/redis_client.py
git commit -m "feat: Redis 连接池 max_connections=50 (多 worker 防 fd 爆, env 可调)"
```

---

## ⑤ 美股 trades 纯 LRU-30

### Task 1: viewed ZSET 写入(sse_bars 注册时 ZADD)

**Files:** Modify `apps/api/routes/sse_bars.py::_register_subscriptions`;Test `tests/unit/api/test_us_viewed_zadd.py`

- [ ] **Step 1: 写失败测试**
```python
# tests/unit/api/test_us_viewed_zadd.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.routes.sse_bars import _register_subscriptions


@pytest.mark.asyncio
async def test_us_symbol_zadds_viewed():
    rc = MagicMock(); rc._r = MagicMock()
    rc._r.set = AsyncMock(); rc._r.zadd = AsyncMock()
    await _register_subscriptions({"AAPL"}, "5m", rc)
    rc._r.zadd.assert_awaited()                       # US → 写 viewed ZSET
    args = rc._r.zadd.await_args
    assert args[0][0] == "state:us:viewed"


@pytest.mark.asyncio
async def test_ashare_symbol_no_zadd():
    rc = MagicMock(); rc._r = MagicMock()
    rc._r.set = AsyncMock(); rc._r.zadd = AsyncMock()
    await _register_subscriptions({"600519.SH"}, "5m", rc)
    rc._r.zadd.assert_not_awaited()                   # 非 US 不写
```

- [ ] **Step 2: 确认失败** — `pytest tests/unit/api/test_us_viewed_zadd.py -v`

- [ ] **Step 3: 实现** — `_register_subscriptions` 里,对 `infer_market(sym)=="us"` 的 symbol 额外 `ZADD state:us:viewed <now> <sym>`。在现有写 `state_bar_subscription` 的循环内加:
```python
            if market == "us":
                try:
                    import time as _t
                    await redis_cache._r.zadd("state:us:viewed", {sym: _t.time()})  # noqa: SLF001
                except Exception as e:  # noqa: BLE001
                    log.warning("sse.us_viewed_zadd_failed", symbol=sym, error=str(e))
```
(`market = infer_market(sym)` 该循环里已有;若没有则补。)

- [ ] **Step 4: 确认通过** — `pytest tests/unit/api/test_us_viewed_zadd.py -v` + `python -c "from apps.api.main import app; print('ok')"`

- [ ] **Step 5: 提交**
```bash
git add apps/api/routes/sse_bars.py tests/unit/api/test_us_viewed_zadd.py
git commit -m "feat: 美股 viewed ZSET (sse_bars 注册时 ZADD, 喂 LRU)"
```

### Task 2: ws_consumer LRU-30 选择 + realtime_active

**Files:** Modify `apps/collector/us/ws_consumer.py::_desired_trade_symbols`;Test `tests/unit/collector/test_us_lru_desired.py`

- [ ] **Step 1: 写失败测试**
```python
# tests/unit/collector/test_us_lru_desired.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.ws_consumer import _desired_trade_symbols


@pytest.mark.asyncio
async def test_desired_reads_zset_top_n_and_sets_active():
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.zremrangebyscore = AsyncMock()
    redis._r.zrevrange = AsyncMock(return_value=[b"AAPL", b"NVDA", b"MSFT"])
    redis._r.delete = AsyncMock(); redis._r.sadd = AsyncMock()
    got = await _desired_trade_symbols(redis, cap=30)
    assert got == {"AAPL", "NVDA", "MSFT"}
    redis._r.zrevrange.assert_awaited_once()           # 取 ZSET top-N
    args = redis._r.zrevrange.await_args
    assert args[0][0] == "state:us:viewed" and args[0][1] == 0 and args[0][2] == 29
    redis._r.sadd.assert_awaited()                     # 维护 realtime_active


@pytest.mark.asyncio
async def test_desired_empty_returns_empty_and_clears_active():
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.zremrangebyscore = AsyncMock()
    redis._r.zrevrange = AsyncMock(return_value=[])
    redis._r.delete = AsyncMock(); redis._r.sadd = AsyncMock()
    got = await _desired_trade_symbols(redis, cap=30)
    assert got == set()
    redis._r.delete.assert_awaited()                   # 清空 active
```

- [ ] **Step 2: 确认失败** — `pytest tests/unit/collector/test_us_lru_desired.py -v`

- [ ] **Step 3: 实现** — 重写 `_desired_trade_symbols`(纯 LRU: 取最近访问 top-cap;裁掉 >10min 陈旧;维护 `state:us:realtime_active`):
```python
async def _desired_trade_symbols(redis, cap: int = 30) -> set[str]:
    """LRU: 取 state:us:viewed(最近访问 ZSET)的 top-cap 订阅 trades。

    免费 IEX trades 限 ~30。裁掉 >10min 未看的陈旧项;维护 state:us:realtime_active
    供前端判断某标的是否有实时分时。
    """
    import time
    try:
        await redis._r.zremrangebyscore(  # noqa: SLF001
            "state:us:viewed", "-inf", time.time() - 600)   # 10min 陈旧裁掉
        members = await redis._r.zrevrange("state:us:viewed", 0, cap - 1)  # noqa: SLF001
        syms = {(m.decode() if isinstance(m, bytes) else m) for m in members}
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.lru_scan_failed", error=str(e))
        return set()
    # 维护 realtime_active 集合(前端 realtime 提示用)
    try:
        await redis._r.delete("state:us:realtime_active")  # noqa: SLF001
        if syms:
            await redis._r.sadd("state:us:realtime_active", *syms)  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.realtime_active_failed", error=str(e))
    return syms
```

- [ ] **Step 4: 确认通过** — `pytest tests/unit/collector/test_us_lru_desired.py -v` + `python -c "from apps.collector.us.main import app; print('ok')"`

- [ ] **Step 5: 提交**
```bash
git add apps/collector/us/ws_consumer.py tests/unit/collector/test_us_lru_desired.py
git commit -m "feat: 美股 trades 纯 LRU-30(state:us:viewed 最近访问) + 维护 realtime_active"
```

### Task 3: api 分时接口带 realtime flag + 前端提示

**Files:** Modify `apps/api/routes/symbols.py::intraday_line`、`apps/web/lib/use_intraday_line.ts`、`apps/web/components/IntradayLineChart.tsx`

- [ ] **Step 1: api intraday_line 加 realtime**
`apps/api/routes/symbols.py` 的 `intraday_line` 路由(约 :289)加 `redis_cache=Depends(get_redis_cache)`,转发拿到 payload 后注入 realtime:
```python
    market = infer_market(symbol)
    realtime = True
    if market == "us":
        try:
            realtime = bool(await redis_cache._r.sismember("state:us:realtime_active", symbol))  # noqa: SLF001
        except Exception:  # noqa: BLE001
            realtime = False
    # ... 转发得到 body(dict)后:
    body["realtime"] = realtime
    return body
```
(intraday_line 现返回 collector 转发的 dict;在 return 前给 dict 加 `realtime` 键。非 US 恒 True。)

- [ ] **Step 2: 前端 hook 暴露 realtime**
`apps/web/lib/use_intraday_line.ts`:响应体读 `realtime`(默认 true),hook 返回值加 `realtime: boolean`。

- [ ] **Step 3: 组件提示**
`apps/web/components/IntradayLineChart.tsx`:从 hook 取 `realtime`;当 `inferMarket(symbol)==='us' && !realtime` 时顶部显示提示:
```tsx
{inferMarket(symbol) === 'us' && !realtime && (
  <div className="text-xs text-amber-400 mb-1">超出实时名额,分时暂不实时更新(K 线正常)</div>
)}
```

- [ ] **Step 4: 验证** — `cd apps/web && npx tsc --noEmit && cd ../..` + `python -c "from apps.api.main import app; print('ok')"`

- [ ] **Step 5: 提交**
```bash
git add apps/api/routes/symbols.py apps/web/lib/use_intraday_line.ts apps/web/components/IntradayLineChart.tsx
git commit -m "feat: 分时接口带 realtime flag + 前端'超出实时名额'提示"
```

---

## ④ 历史分页 Redis 页缓存

### Task: bars_history 加页缓存

**Files:** Modify `core/cache/keys.py`(加 cache_barspage)、`apps/api/routes/symbols.py::bars_history`;Test `tests/unit/cache/test_keys.py`(追加)、`tests/unit/api/test_bars_history_cache.py`

- [ ] **Step 1: keys 加 cache_barspage + 测试**
`tests/unit/cache/test_keys.py` 追加:
```python
def test_cache_barspage_key():
    from core.cache import keys
    k = keys.cache_barspage("us", "AAPL", "5m", "2026-06-01T00:00:00", 500)
    assert k == "cache:barspage:us:AAPL:5m:2026-06-01T00:00:00:500"
    keys.validate(k)

def test_cache_barspage_latest_uses_none_marker():
    from core.cache import keys
    k = keys.cache_barspage("us", "AAPL", "5m", None, 500)
    assert k.endswith(":latest:500")
```
实现 `core/cache/keys.py`(cache 区加):
```python
def cache_barspage(market: str, symbol: str, interval: str, before: str | None, limit: int) -> str:
    """历史分页页缓存。游标页(before 非空)不可变长 TTL; 最新页(latest)短 TTL。"""
    cursor = before if before else "latest"
    return f"cache:barspage:{market}:{symbol}:{interval}:{cursor}:{limit}"
```
(确认 `cache:` namespace 已被 `keys.validate` 接受;若 validate 有前缀白名单,把 `barspage` 加进去。)

- [ ] **Step 2: bars_history 加缓存(测试)**
`tests/unit/api/test_bars_history_cache.py`:
```python
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_history_cache_hit_skips_collector(monkeypatch):
    from apps.api.routes import symbols as m
    rc = MagicMock(); rc._r = MagicMock()
    cached = {"bars": [], "meta": {"stale": False}}
    rc._r.get = AsyncMock(return_value=json.dumps(cached).encode())
    client = MagicMock(); client.get = AsyncMock()   # 不应被调用
    out = await m.bars_history("AAPL", interval="5m", before="2026-06-01T00:00:00",
                               limit=500, client=client, redis_cache=rc)
    client.get.assert_not_awaited()                  # 命中缓存 → 不打 collector

@pytest.mark.asyncio
async def test_history_cache_miss_forwards_and_caches(monkeypatch):
    from apps.api.routes import symbols as m
    rc = MagicMock(); rc._r = MagicMock()
    rc._r.get = AsyncMock(return_value=None)         # miss
    rc._r.set = AsyncMock()
    resp = MagicMock(); resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"bars": [], "meta": {}})
    client = MagicMock(); client.get = AsyncMock(return_value=resp)
    await m.bars_history("AAPL", interval="5m", before="2026-06-01T00:00:00",
                         limit=500, client=client, redis_cache=rc)
    client.get.assert_awaited_once()                 # miss → 转发
    rc._r.set.assert_awaited()                       # 回写缓存
```

- [ ] **Step 3: 实现** — `bars_history` 加 `redis_cache=Depends(get_redis_cache)` 参数。在 `params` 构造后、转发前查缓存;转发成功后回写:
```python
    import json
    cache_key = keys.cache_barspage(market, symbol, interval, before, limit)
    try:
        cached = await redis_cache._r.get(cache_key)  # noqa: SLF001
        if cached:
            payload = json.loads(cached)
            return BarsResponse(symbol=symbol, interval=interval,
                                bars=[BarDTO(**b) for b in payload.get("bars", [])],
                                meta=BarsResponseMeta(stale=False))
    except Exception:  # noqa: BLE001
        pass
    # ... 现有转发逻辑得到 payload(成功)后, 回写缓存:
    try:
        ttl = 86400 if before else 30   # 游标页(历史不可变)长 TTL; 最新页短 TTL
        await redis_cache._r.set(cache_key, json.dumps({"bars": raw, "meta": meta}), ex=ttl)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
```
(顶部确认 import `keys`、`json`;`raw`/`meta` 是现有转发后变量。collector 不可达的 stale 分支**不缓存**。)

- [ ] **Step 4: 验证** — `pytest tests/unit/cache/test_keys.py tests/unit/api/test_bars_history_cache.py -v` + `python -c "from apps.api.main import app; print('ok')"`

- [ ] **Step 5: 提交**
```bash
git add core/cache/keys.py apps/api/routes/symbols.py tests/unit/cache/test_keys.py tests/unit/api/test_bars_history_cache.py
git commit -m "feat: 历史分页 Redis 页缓存(游标页长TTL/最新页短TTL, 卸载 collector)"
```

---

## 收尾验证

- [ ] `pytest -m "not integration" -q`(除既有 index_minute 2 例外全绿)
- [ ] `cd apps/web && npx tsc --noEmit`
- [ ] 4 进程 import OK
- [ ] 重启 us collector + api,冒烟健康
- [ ] 实库快查:`docker exec marketpulse-redis-dev redis-cli zcard state:us:viewed`(盘中有人看美股时 >0)、`scard state:us:realtime_active`(≤30)
