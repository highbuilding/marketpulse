# 动态自选即时采集 + K线首屏闪烁修复 实施计划

> **For agentic workers:** 设计见 `docs/superpowers/specs/2026-06-03-dynamic-watchlist-refill-and-kline-flicker-design.md`。Steps 用 checkbox 跟踪。

**Goal:** 加自选时发 refill 让 collector 立即拉该标的历史(修 MRVL 类动态标的无数据)+ 修 K线切周期"先一根后一堆"闪烁。

**Architecture:** ① `add_symbol` 的后台任务从 `_initial_scan`(api 自扫,但 api 无 DuckDB → 拉不到 bar,正是 MRVL 没数据根因)改为发 `bus:bars.refill_request`(复用 symbols.py 现成 `_publish_refill_request`,collector 拉 bar 入库,scan consumer 随后出信号)。② 前端 `displayBars` 首屏 hist 未到时不渲染 stream 单根。

**Tech Stack:** FastAPI、Redis Streams(refill bus)、现成 refill consumer、Next.js。

**根因修正(相对 spec §1)**:spec 写"新增发送";实测发现 `add_symbol` 已有 `_initial_scan` 后台任务,但它调 `scan_symbol`→`fetch_fresh_bars`,而 **api 进程 KLineService.repo=None(雷区6 不连 DuckDB)→ 拉不到也写不了 bar**。所以 MRVL 扫了个空。修法:`_initial_scan` 改为发 refill(复用 `_publish_refill_request`,白名单 CORE∪watchlist 已含刚加的标的)。

---

## 文件结构

**改动:**
- `apps/api/routes/watchlists.py`:`_initial_scan` → 发 refill(`_publish_refill_request`),覆盖 SIGNAL_INTERVALS + 5m
- `apps/web/app/symbol/[code]/page.tsx`:`displayBars` 首屏 gate

**复用(不改):** `apps/api/routes/symbols.py::_publish_refill_request`、`bus:bars.refill_request`、各 collector refill consumer

---

## Task 1: add_symbol 发 refill 替代无效的 api 自扫

**Files:**
- Modify: `apps/api/routes/watchlists.py`

- [ ] **Step 1: 改 _initial_scan 为发 refill**

把 `_initial_scan`(api 进程 scan,拉不到 bar)替换为后台发 refill 请求。watchlists.py 顶部确认 import `get_redis_cache`、`infer_market`、`SIGNAL_INTERVALS`、`_publish_refill_request`(从 symbols 导入或复制一份)。

新后台函数(替换 `_initial_scan`):
```python
async def _refill_new_symbol(symbol: str, redis_cache, watchlist) -> None:
    """新加自选: 发 refill 让对应市场 collector 立即拉该标的历史 bar。
    api 进程无 DuckDB(雷区6), 不能自己拉, 必须经 refill 交给 collector。
    覆盖信号周期 + 5m(详情页默认)。fire-and-forget。"""
    from apps.api.routes.symbols import _publish_refill_request
    from core.domain.intervals import SIGNAL_INTERVALS
    # 各周期合理回补窗口(天): intraday 短, 1d 长
    days_map = {"5m": 30, "15m": 30, "30m": 60, "60m": 120, "4h": 365, "1d": 1825}
    for iv in ("5m", *SIGNAL_INTERVALS):
        try:
            await _publish_refill_request(redis_cache, symbol, iv,
                                          days_map.get(iv, 60), watchlist=watchlist)
        except Exception as e:  # noqa: BLE001
            log.warning("watchlist.refill_failed", symbol=symbol, interval=iv, error=str(e))
```

改 add_symbol 注入 redis + watchlist,后台任务换成 `_refill_new_symbol`:
```python
@router.post("/{wl_id}/symbols", status_code=204)
async def add_symbol(wl_id: int, body: AddSymbolBody,
                     bg: BackgroundTasks,
                     svc: WatchlistService = Depends(get_watchlist_service),
                     redis_cache=Depends(get_redis_cache)) -> None:
    await svc.add_symbol(wl_id, body.symbol)
    bg.add_task(_refill_new_symbol, body.symbol, redis_cache, svc)
```
(移除原 `scan: SignalScanService` 依赖和 `_initial_scan` 调用;`_initial_scan` 函数可删或保留不引用。)

- [ ] **Step 2: import 测试**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; print('OK')"`
Expected: OK

- [ ] **Step 3: 重启 api + 端到端验证(MRVL)**

```bash
# 重启 api
pkill -9 -f "uvicorn apps.api.main:app"; sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 & disown
sleep 7
# 触发: 重新加一次 MRVL(已在自选则先删再加, 或直接发 refill 验证链路)
curl -s -X POST http://127.0.0.1:8787/api/watchlists/1/symbols -H 'Content-Type: application/json' -d '{"symbol":"MRVL"}' -w " add=%{http_code}\n"
sleep 15
# 查库 MRVL bar 是否入库
```
Expected: collector refill consumer 拉 MRVL,库里出现 MRVL bar(下一步脚本查)

- [ ] **Step 4: 验证 MRVL 入库**

```bash
. .venv/bin/activate && python3 -c "
import duckdb
con=duckdb.connect('data/bars_us.duckdb',read_only=True)
r=con.execute(\"SELECT interval,COUNT(*) FROM bars WHERE symbol='MRVL' GROUP BY interval\").fetchall()
print('MRVL bar:', dict((x[0],x[1]) for x in r) or '仍无')
"
```
Expected: MRVL 各周期有 bar(15m/30m/60m/4h/1d/5m)

- [ ] **Step 5: commit**

```bash
git add apps/api/routes/watchlists.py
git commit -m "fix(api): 加自选发 refill 让 collector 拉历史(替代 api 自扫, 修动态标的无数据)"
```

---

## Task 2: K线首屏 gate 修闪烁

**Files:**
- Modify: `apps/web/app/symbol/[code]/page.tsx`

- [ ] **Step 1: 改 displayBars 首屏 gate**

`page.tsx` 的 displayBars(75-77):
```js
const displayBars: BarDTO[] = useMemo(() => {
  // 首屏历史(REST 500根)未到时不渲染 stream 单根, 消除切周期"先一根后一堆"闪烁
  return hist.bars.length === 0 ? [] : mergeBarsAsc(hist.bars, streamBars)
}, [hist.bars, streamBars])
```

- [ ] **Step 2: tsc 验证**

Run: `cd apps/web && npx tsc --noEmit`
Expected: exit 0

- [ ] **Step 3: 走查**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/symbol/MRVL`
Expected: 200;手动切周期不再"先一根后一堆"(空→完整 500 根)

- [ ] **Step 4: commit**

```bash
git add apps/web/app/symbol/[code]/page.tsx
git commit -m "fix(web): K线切周期首屏 gate, hist 未到不渲染 stream 单根(消除闪烁)"
```

---

## Task 3: 单测(加自选发 refill)

**Files:**
- Create: `tests/unit/api/test_watchlist_add_refill.py`

- [ ] **Step 1: 写测试**

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_refill_new_symbol_publishes_all_intervals():
    from apps.api.routes.watchlists import _refill_new_symbol
    calls = []
    async def fake_pub(rc, sym, iv, days, *, watchlist=None):
        calls.append(iv)
    with patch("apps.api.routes.symbols._publish_refill_request", fake_pub):
        await _refill_new_symbol("MRVL", object(), None)
    # 覆盖 5m + 信号周期
    assert "5m" in calls and "1d" in calls and "4h" in calls
    assert len(calls) >= 5
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/unit/api/test_watchlist_add_refill.py -v`
Expected: PASS（若 patch 路径因 import 方式不命中,改 patch `apps.api.routes.watchlists._publish_refill_request` 并相应在模块顶部 import）

- [ ] **Step 3: commit**

```bash
git add tests/unit/api/test_watchlist_add_refill.py
git commit -m "test(api): 加自选发 refill 覆盖各周期"
```
