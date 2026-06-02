# 多用户化 ② SSE hub(单读多分发)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax。

**Goal:** 把"每条 SSE 连接各自 `xread` 全局流再过滤"(O(连接数×消息) CPU + 每连接一条 Redis 连接)改成"每 worker 一个 hub 单读、解析一次、按 symbol 分发给进程内订阅者队列"(O(消息) + 每 hub 一条 Redis 连接)。

**Architecture:** `StreamHub` 每 Redis 流一个(bars / intraday),api lifespan 起一个 `run()` 后台任务读流,解析一次按 `key_fn(payload)` 分发给 `Subscriber`(有界队列、满丢最旧)。SSE 端点注册 Subscriber → 循环 `await sub.get()`(超时发 ping)→ `finally` 注销(资源释放)。多 worker:各 worker 独立 hub 从 `$` 读全量、分发给本地连接,无需 sticky。

**Tech Stack:** FastAPI/Starlette、asyncio、Redis Streams、pytest。

**Spec:** `docs/superpowers/specs/2026-06-02-multiuser-scaling-design.md` §3。

**稳健性要点(本计划刻意保证)**:① 断开必 `unregister`(`finally`,无连接泄漏);② 背压:慢消费者队列满丢最旧帧,**绝不阻塞 hub 分发**;③ hub `xread` 异常退避重连、`CancelledError` 干净退出、坏消息跳过;④ **注册先于 init 快照**,消除"快照与订阅之间漏消息"的窗口;⑤ 分发迭代订阅集快照,防迭代中变更。

---

## 文件结构

**新建:** `apps/api/sse_hub.py`(`Subscriber` + `StreamHub` + `bars_key`/`intraday_key`)、`tests/unit/api/test_sse_hub.py`、`tests/unit/api/test_sse_hub_run.py`
**改造:** `apps/api/main.py`(lifespan 起/停 hub,挂 app.state)、`apps/api/routes/sse_bars.py`、`apps/api/routes/sse_intraday.py`

---

## Task 1: Subscriber + StreamHub 注册/分发/注销

**Files:** Create `apps/api/sse_hub.py`;Test `tests/unit/api/test_sse_hub.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_sse_hub.py
import asyncio
import pytest
from apps.api.sse_hub import Subscriber, StreamHub, bars_key, intraday_key


def test_key_fns():
    assert bars_key({"symbol": "AAPL", "interval": "5m"}) == ("AAPL", "5m")
    assert intraday_key({"symbol": "AAPL"}) == "AAPL"


@pytest.mark.asyncio
async def test_subscriber_offer_drop_oldest_when_full():
    sub = Subscriber(maxsize=2)
    sub.offer({"n": 1}); sub.offer({"n": 2}); sub.offer({"n": 3})   # 满后丢最旧(n=1)
    a = await sub.get(); b = await sub.get()
    assert [a["n"], b["n"]] == [2, 3]


def test_register_dispatch_only_to_matching_key():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    sub_a = hub.register([("AAPL", "5m")])
    sub_b = hub.register([("MSFT", "5m")])
    n = hub.dispatch({"symbol": "AAPL", "interval": "5m", "final": False})
    assert n == 1
    assert sub_a._q.qsize() == 1 and sub_b._q.qsize() == 0   # 只投 A


def test_register_multi_key_one_subscriber_batch():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    sub = hub.register([("AAPL", "5m"), ("MSFT", "5m")])      # batch: 一个 sub 多 key
    hub.dispatch({"symbol": "AAPL", "interval": "5m"})
    hub.dispatch({"symbol": "MSFT", "interval": "5m"})
    assert sub._q.qsize() == 2


def test_unregister_stops_delivery_and_cleans_key():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    sub = hub.register([("AAPL", "5m")])
    hub.unregister([("AAPL", "5m")], sub)
    assert hub.dispatch({"symbol": "AAPL", "interval": "5m"}) == 0
    assert ("AAPL", "5m") not in hub._registry                # 空 key 清理


def test_dispatch_bad_payload_no_crash():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    assert hub.dispatch({}) == 0                               # key=(None,None) 无订阅
```

- [ ] **Step 2: 运行确认失败** — `. .venv/bin/activate && pytest tests/unit/api/test_sse_hub.py -v`(ModuleNotFoundError)

- [ ] **Step 3: 实现** `apps/api/sse_hub.py`:

```python
"""SSE 单读多分发 hub: 每 worker 一个 run() 读 Redis 流, 解析一次按 symbol 分发。

把"每连接各自 xread 全局流再过滤"(O(连接×消息))降到 O(消息);
Redis 连接 = 每 hub 1 条。多 worker: 各 worker 独立 hub 从 $ 读全量。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Hashable, Iterable

import structlog

log = structlog.get_logger(__name__)

DEFAULT_QUEUE_MAX = 100
_BATCH = 50
_BLOCK_MS = 1000   # hub xread 阻塞窗口(短 → cancel 响应快)


class Subscriber:
    """单 SSE 连接的收件箱: 有界队列, 满则丢最旧(进行中态丢帧无害, 绝不阻塞 hub)。"""

    def __init__(self, maxsize: int = DEFAULT_QUEUE_MAX) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    def offer(self, item: dict) -> None:
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._q.get_nowait()       # 丢最旧
            except asyncio.QueueEmpty:
                pass
            try:
                self._q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    async def get(self) -> dict:
        return await self._q.get()


class StreamHub:
    def __init__(self, redis, channel: str, key_fn: Callable[[dict], Hashable]) -> None:
        self._redis = redis
        self._channel = channel
        self._key_fn = key_fn
        self._registry: dict[Hashable, set[Subscriber]] = {}
        self._stopped = False

    def register(self, keys: Iterable[Hashable], maxsize: int = DEFAULT_QUEUE_MAX) -> Subscriber:
        sub = Subscriber(maxsize)
        for k in keys:
            self._registry.setdefault(k, set()).add(sub)
        return sub

    def unregister(self, keys: Iterable[Hashable], sub: Subscriber) -> None:
        for k in keys:
            s = self._registry.get(k)
            if s:
                s.discard(sub)
                if not s:
                    self._registry.pop(k, None)

    def dispatch(self, payload: dict) -> int:
        """解析好的 payload 投给注册了该 key 的所有 Subscriber。返回投递数。"""
        try:
            key = self._key_fn(payload)
        except Exception:  # noqa: BLE001
            return 0
        subs = self._registry.get(key)
        if not subs:
            return 0
        for sub in list(subs):     # 快照, 防迭代中变更
            sub.offer(payload)
        return len(subs)

    def stop(self) -> None:
        self._stopped = True


def bars_key(payload: dict):
    return (payload.get("symbol"), payload.get("interval"))


def intraday_key(payload: dict):
    return payload.get("symbol")
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_sse_hub.py -v`(6 PASS)

- [ ] **Step 5: 提交**

```bash
git add apps/api/sse_hub.py tests/unit/api/test_sse_hub.py
git commit -m "feat: SSE StreamHub 注册/分发/注销 + Subscriber 背压(丢最旧)"
```

---

## Task 2: StreamHub.run() 读流循环

**Files:** Modify `apps/api/sse_hub.py`(加 `run`);Test `tests/unit/api/test_sse_hub_run.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_sse_hub_run.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.sse_hub import StreamHub, bars_key


def _entry(payload):
    return [(b"chan", [(b"1-0", {b"data": json.dumps(payload).encode()})])]


@pytest.mark.asyncio
async def test_run_dispatches_then_cancels():
    redis = MagicMock(); redis._r = MagicMock()
    # 第一次 xread 返回一条, 第二次抛 CancelledError 让 run 退出
    redis._r.xread = AsyncMock(side_effect=[
        _entry({"symbol": "AAPL", "interval": "5m", "final": False}),
        asyncio.CancelledError(),
    ])
    hub = StreamHub(redis, "chan", bars_key)
    sub = hub.register([("AAPL", "5m")])
    await hub.run()                       # 处理一条后被 cancel 干净退出
    assert sub._q.qsize() == 1


@pytest.mark.asyncio
async def test_run_skips_malformed_and_continues():
    redis = MagicMock(); redis._r = MagicMock()
    bad = [(b"chan", [(b"1-0", {b"data": b"not-json"})])]
    good = _entry({"symbol": "AAPL", "interval": "5m"})
    redis._r.xread = AsyncMock(side_effect=[bad, good, asyncio.CancelledError()])
    hub = StreamHub(redis, "chan", bars_key)
    sub = hub.register([("AAPL", "5m")])
    await hub.run()
    assert sub._q.qsize() == 1            # 坏消息跳过, 好消息照投


@pytest.mark.asyncio
async def test_run_retries_on_read_error(monkeypatch):
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.xread = AsyncMock(side_effect=[
        RuntimeError("redis down"),       # 读失败 → 退避重试
        _entry({"symbol": "AAPL", "interval": "5m"}),
        asyncio.CancelledError(),
    ])
    monkeypatch.setattr("apps.api.sse_hub.asyncio.sleep", AsyncMock())
    hub = StreamHub(redis, "chan", bars_key)
    sub = hub.register([("AAPL", "5m")])
    await hub.run()
    assert sub._q.qsize() == 1            # 错误后重试成功
```

- [ ] **Step 2: 运行确认失败** — `pytest tests/unit/api/test_sse_hub_run.py -v`(AttributeError: run)

- [ ] **Step 3: 实现** — `apps/api/sse_hub.py` 给 `StreamHub` 加方法:

```python
    async def run(self) -> None:
        """单读循环: xread 全量 → 解析一次 → dispatch。每 worker 一个。"""
        log.info("sse_hub.started", channel=self._channel)
        last_id = "$"
        while not self._stopped:
            try:
                entries = await self._redis._r.xread(  # noqa: SLF001
                    streams={self._channel: last_id}, count=_BATCH, block=_BLOCK_MS)
            except asyncio.CancelledError:
                log.info("sse_hub.cancelled", channel=self._channel)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("sse_hub.read_failed", channel=self._channel, error=str(e))
                await asyncio.sleep(1)
                continue
            if not entries:
                continue
            for _stream, msgs in entries:
                for msg_id, fields in msgs:
                    last_id = msg_id
                    try:
                        raw = fields.get(b"data") or fields.get("data")
                        if raw is None:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        self.dispatch(json.loads(raw))
                    except asyncio.CancelledError:
                        return
                    except Exception as e:  # noqa: BLE001
                        log.warning("sse_hub.parse_failed",
                                    channel=self._channel, error=str(e))
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_sse_hub_run.py -v`(3 PASS)

- [ ] **Step 5: 提交**

```bash
git add apps/api/sse_hub.py tests/unit/api/test_sse_hub_run.py
git commit -m "feat: StreamHub.run 单读循环(解析一次分发, 退避重连, 坏消息跳过)"
```

---

## Task 3: lifespan 起/停 hub + 挂 app.state

**Files:** Modify `apps/api/main.py`;验证 import

- [ ] **Step 1: 改 lifespan** — `apps/api/main.py` 的 `lifespan`(`:36-52`)内,`state_repo.init()` 之后、`yield` 之前加:

```python
    # SSE hub: 每 worker 一个读流任务, 解析一次按 symbol 分发(替代每连接各自 xread)
    import asyncio
    from apps.api.sse_hub import StreamHub, bars_key, intraday_key
    from core.cache import keys as _keys
    _rc = get_redis_cache()
    app.state.bars_hub = StreamHub(_rc, _keys.BUS_BARS_UPDATED, bars_key)
    app.state.intraday_hub = StreamHub(_rc, _keys.BUS_INTRADAY_UPDATED, intraday_key)
    _hub_tasks = [
        asyncio.create_task(app.state.bars_hub.run(), name="sse_bars_hub"),
        asyncio.create_task(app.state.intraday_hub.run(), name="sse_intraday_hub"),
    ]
    log.info("sse_hubs.started")
```

`yield` 之后(`log.info("api.stopped")` 之前)加:

```python
    app.state.bars_hub.stop()
    app.state.intraday_hub.stop()
    for _t in _hub_tasks:
        _t.cancel()
    await asyncio.gather(*_hub_tasks, return_exceptions=True)
```

- [ ] **Step 2: 验证 import** — `python -c "from apps.api.main import app; print('ok')"`

- [ ] **Step 3: 提交**

```bash
git add apps/api/main.py
git commit -m "feat: api lifespan 起/停 bars/intraday SSE hub (挂 app.state)"
```

---

## Task 4: sse_bars 改用 hub(单 + batch)

**Files:** Modify `apps/api/routes/sse_bars.py`;Test `tests/unit/api/test_sse_bars_gen.py`

核心:`_stream_gen` 不再自己 xread;改 `hub.register([(sym,interval)...])` → `finally hub.unregister`;循环 `await asyncio.wait_for(sub.get(), timeout=PING)`,超时发 ping。**注册先于 init 快照**(消除漏窗口)。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_sse_bars_gen.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.sse_hub import StreamHub, bars_key
from apps.api.routes.sse_bars import _stream_gen


@pytest.mark.asyncio
async def test_stream_gen_yields_connected_then_tick_then_unregisters():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    redis_cache = MagicMock()
    redis_cache.get_msgpack = AsyncMock(return_value=None)   # 无 init 快照
    gen = _stream_gen({"AAPL"}, "5m", hub, redis_cache)

    out = []
    first = await gen.__anext__()           # connected
    out.append(first)
    # 注册后投一条 tick → 应被 yield 成 'tick'
    hub.dispatch({"symbol": "AAPL", "interval": "5m", "final": False, "close": 1})
    second = await gen.__anext__()
    out.append(second)
    assert b"event: connected" in out[0]
    assert b"event: tick" in out[1]
    # 关闭生成器 → finally 注销
    await gen.aclose()
    assert ("AAPL", "5m") not in hub._registry   # 资源释放
```

- [ ] **Step 2: 运行确认失败** — `pytest tests/unit/api/test_sse_bars_gen.py -v`(签名不符 / 仍自己 xread)

- [ ] **Step 3: 实现** — 重写 `apps/api/routes/sse_bars.py` 的 `_stream_gen` 及两个端点。保留 `_sse_event` / `infer_market` / `PING_INTERVAL_S`:

```python
async def _stream_gen(symbols: set[str], interval: str, hub, redis_cache):
    """注册 hub → 先发 connected + init 快照 → 循环取队列(超时发 ping)→ finally 注销。"""
    from datetime import datetime, timezone
    keys_list = [(s, interval) for s in symbols]
    sub = hub.register(keys_list)            # 先注册, 消除 init 与订阅间漏窗口
    try:
        server_ts = datetime.now(timezone.utc).isoformat()
        yield _sse_event("connected", {"symbols": list(symbols),
                                       "interval": interval, "server_ts": server_ts})
        # init: 当前进行中 bar 快照(各 symbol)
        for sym in symbols:
            try:
                cur = await redis_cache.get_msgpack(
                    keys.cache_bars_current(infer_market(sym), sym, interval))
            except Exception:  # noqa: BLE001
                cur = None
            if cur:
                yield _sse_event("init", {"bars": [cur], "symbol": sym, "server_ts": server_ts})
        while True:
            try:
                payload = await asyncio.wait_for(sub.get(), timeout=PING_INTERVAL_S)
            except asyncio.TimeoutError:
                yield _sse_event("ping", {"server_ts": datetime.now(timezone.utc).isoformat()})
                continue
            except asyncio.CancelledError:
                return
            event = "bar" if payload.get("final") else "tick"
            yield _sse_event(event, payload)
    finally:
        hub.unregister(keys_list, sub)       # 断开必注销(资源释放)


@router.get("/bars/batch")
async def sse_bars_batch(request: Request,
                         symbols: str = Query(...), interval: str = Query("5m"),
                         redis_cache=Depends(get_redis_cache)):
    syms = {s.strip() for s in symbols.split(",") if s.strip()}
    if not syms:
        return StreamingResponse(_empty_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})
    hub = request.app.state.bars_hub
    return StreamingResponse(
        _stream_gen(syms, interval, hub, redis_cache), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.get("/bars/{symbol}/{interval}")
async def sse_bars(request: Request, symbol: str, interval: str,
                   redis_cache=Depends(get_redis_cache)):
    hub = request.app.state.bars_hub
    return StreamingResponse(
        _stream_gen({symbol}, interval, hub, redis_cache), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
```

顶部 import 补 `import asyncio`、`from fastapi import APIRouter, Depends, Query, Request`(确认已有则不重复)。删除旧的 `xread` 游标逻辑与 `_BLOCK` 等不再用的部分,保留 `_empty_gen`。

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_sse_bars_gen.py -v` + `python -c "from apps.api.main import app; print('ok')"`

- [ ] **Step 5: 提交**

```bash
git add apps/api/routes/sse_bars.py tests/unit/api/test_sse_bars_gen.py
git commit -m "feat: sse_bars 改用 hub(注册先于init/超时ping/finally注销)"
```

---

## Task 5: sse_intraday 改用 hub

**Files:** Modify `apps/api/routes/sse_intraday.py`;Test `tests/unit/api/test_sse_intraday_gen.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/api/test_sse_intraday_gen.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.api.sse_hub import StreamHub, intraday_key
from apps.api.routes.sse_intraday import _gen


@pytest.mark.asyncio
async def test_intraday_gen_connected_then_point_then_unregister():
    hub = StreamHub(redis=None, channel="c", key_fn=intraday_key)
    redis_cache = MagicMock()
    redis_cache.get_msgpack = AsyncMock(return_value=None)
    gen = _gen("AAPL", hub, redis_cache)
    first = await gen.__anext__()                  # connected
    hub.dispatch({"symbol": "AAPL", "price": 1.0, "avg_price": 1.0})
    second = await gen.__anext__()                 # point
    assert b"event: connected" in first
    assert b"event: point" in second
    await gen.aclose()
    assert "AAPL" not in hub._registry             # 注销
```

- [ ] **Step 2: 运行确认失败** — `pytest tests/unit/api/test_sse_intraday_gen.py -v`

- [ ] **Step 3: 实现** — 重写 `apps/api/routes/sse_intraday.py` 的 `_gen` + 端点:

```python
async def _gen(symbol: str, hub, redis_cache):
    import asyncio
    from datetime import datetime, timezone
    market = infer_market(symbol)
    sub = hub.register([symbol])
    try:
        yield _sse_event("connected", {"symbol": symbol,
                                       "server_ts": datetime.now(timezone.utc).isoformat()})
        try:
            cur = await redis_cache.get_msgpack(keys.cache_intraday_current(market, symbol))
        except Exception:  # noqa: BLE001
            cur = None
        if cur:
            yield _sse_event("init", {"point": cur, "symbol": symbol})
        while True:
            try:
                payload = await asyncio.wait_for(sub.get(), timeout=PING_INTERVAL_S)
            except asyncio.TimeoutError:
                yield _sse_event("ping", {"server_ts": datetime.now(timezone.utc).isoformat()})
                continue
            except asyncio.CancelledError:
                return
            yield _sse_event("point", payload)
    finally:
        hub.unregister([symbol], sub)


@router.get("/intraday/{symbol}")
async def sse_intraday(request: Request, symbol: str, redis_cache=Depends(get_redis_cache)):
    hub = request.app.state.intraday_hub
    return StreamingResponse(
        _gen(symbol, hub, redis_cache), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
```

顶部 import 补 `from fastapi import APIRouter, Depends, Request`(`asyncio` 已有)。删旧 xread 逻辑。

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/api/test_sse_intraday_gen.py -v` + `python -c "from apps.api.main import app; print('ok')"`

- [ ] **Step 5: 提交**

```bash
git add apps/api/routes/sse_intraday.py tests/unit/api/test_sse_intraday_gen.py
git commit -m "feat: sse_intraday 改用 hub(注册先于init/超时ping/finally注销)"
```

---

## 收尾验证

- [ ] `pytest tests/unit/api/ -q`(hub + 端点 gen 全绿)
- [ ] 全套 `pytest -m "not integration" -q`(除既有 index_minute 2 例)
- [ ] `python -c "from apps.api.main import app; print('OK')"`
- [ ] 重启 api,日志确认 `sse_hubs.started`;`/api/health` 200
- [ ] **多连接隔离冒烟**(curl 两条 SSE):
  - `curl -N http://localhost:8787/api/sse/bars/BTC-USDT/5m`(crypto 盘中有进行中态)→ 应只收到 BTC 的 tick/bar,**收不到其它 symbol**。
  - 同时另开一条不同 symbol,确认互不串台。
  - Ctrl-C 断开后,日志无异常;`docker exec ... redis-cli` 看 Redis 连接数不随每条 SSE 线性增长(应只 hub 占固定 2 条 + 池少量)。
- [ ] **Playwright 证据式验证**(memory `feedback_playwright_evidence_testing`):盘中开个股详情,拦 `/api/sse/bars`,确认进行中态仍在跳(行为不变,只是后端路由换了)。
