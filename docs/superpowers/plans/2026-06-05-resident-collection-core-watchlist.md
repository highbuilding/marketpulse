# 采集模型重构(固定CORE/5m+1d直取/其余聚合)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development 或 executing-plans。设计见 `docs/superpowers/specs/2026-06-05-resident-collection-core-watchlist-design.md`。Steps 用 checkbox 跟踪。仅改 collector,不碰 crypto/api/前端/DB schema。

**Goal:** A股/美股采集改为对固定 CORE_SYMBOLS 常驻(去订阅驱动),源头只直取 5m(+1d),15m/30m/60m/4h 全从 5m 聚合,修聚合触发可靠性,前端 refill 加 CORE 白名单。

**Architecture:** bar_poller 标的集从「默认指数+SSE订阅扫描」改为「CORE_SYMBOLS 常驻」,直取周期砍到只 5m;aggregate 的 15m/30m 聚合路径已存在(159-160行),打开 window 传参即启用;crypto 不动(WS 原生)。

**Tech Stack:** Python/asyncio、APScheduler、DuckDB、Redis、sina(A股)/SIP(美股)。

**关键发现(降低实现量):**
- `aggregate_and_publish` 已含 15m/30m 聚合路径(`("15m","5m",15)`/`("30m","5m",30)`,159-160行),默认 `_NOOP` 关闭。启用 = 调用时传 `window_15m/window_30m`,**不用新写聚合**。
- A股 `_scan_subscriptions` 返回 active 集 = `_DEFAULT_SYMBOLS×_DEFAULT_INTERVALS + 扫订阅`;`_sync_tasks` 已能 start/stop。改 active 来源即可。

---

## 文件结构

**改动(仅 collector):**
- `apps/collector/ashare/bar_poller.py`:active 集 = CORE_SYMBOLS×{5m};去订阅扫描
- `apps/collector/us/bar_poller.py`:_scan_symbols = CORE_SYMBOLS['us']
- `apps/collector/ashare/bar_poller.py` + `us/bar_poller.py`:5m 收线触发聚合时传 15m/30m window(启用聚合)
- `apps/api/routes/watchlists.py`:refill 加 CORE 白名单校验

**不碰**:crypto collector、api 读路径、前端、core 算法、DB schema、1wk/1mo 口径。

---

## Task 1: A股 bar_poller 改 CORE 常驻 + 只直取 5m

**Files:**
- Modify: `apps/collector/ashare/bar_poller.py`
- Test: `tests/unit/collector/test_ashare_poller_core.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_ashare_poller_core.py
def test_active_set_is_core_5m_only():
    from apps.collector.ashare import bar_poller as bp
    from core.domain.core_symbols import CORE_SYMBOLS
    # _build_core_active() 返回 CORE×{5m} 的 task_key 集
    active = bp._build_core_active()
    expected = {f"{s}:5m" for s in CORE_SYMBOLS["ashare"]}
    assert active == expected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_ashare_poller_core.py -v`
Expected: FAIL（`_build_core_active` 不存在）

- [ ] **Step 3: 实现 _build_core_active + 改 _scan_subscriptions**

bar_poller.py 顶部加 import:
```python
from core.domain.core_symbols import CORE_SYMBOLS
```
加函数(模块级):
```python
def _build_core_active() -> set[str]:
    """CORE 标的常驻轮询集: 仅直取 5m(15m/30m/60m/4h 由 5m 聚合)。"""
    return {f"{s}:5m" for s in CORE_SYMBOLS["ashare"]}
```
把 `_scan_subscriptions` 的返回改为常驻 CORE(去掉订阅扫描):
```python
async def _scan_subscriptions(self) -> set[str]:
    """采集集 = CORE 常驻(与前端订阅解耦)。仅 5m 直取。"""
    return _build_core_active()
```
(保留方法名,_sync_tasks 不变;`_is_default` 改为 `symbol in CORE_SYMBOLS["ashare"] and interval == "5m"`。)

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/collector/test_ashare_poller_core.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add apps/collector/ashare/bar_poller.py tests/unit/collector/test_ashare_poller_core.py
git commit -m "feat(collector): A股 bar_poller 改 CORE 常驻, 只直取 5m"
```

---

## Task 2: A股 5m 收线启用 15m/30m 聚合

**Files:**
- Modify: `apps/collector/ashare/bar_poller.py`(_poll_one 末尾 aggregate_and_publish 调用)

- [ ] **Step 1: 改聚合调用,加 15m/30m targets**

`_poll_one` 中 `interval == "5m"` 触发聚合处(原 `targets=("60m","4h")`),改为传 15m/30m/60m/4h 全部 window(增量):
```python
if interval == "5m":
    from apps.collector.jobs.aggregate_derived import aggregate_and_publish
    await aggregate_and_publish(
        self._repo, self._redis, "ashare", symbol,
        targets=("15m", "30m", "60m", "4h"), now=now,
    )
```
确认 `aggregate_and_publish` 的 `targets` 参数会映射到对应 `window_15m=...` 增量(看其签名 195-196 行 `_INCR` 默认 window=2)。若 `aggregate_and_publish` 用 targets 元组驱动,直接加 15m/30m;若用独立 window kwargs,则传 `window_15m=2, window_30m=2`。

- [ ] **Step 2: import 测试**

Run: `. .venv/bin/activate && python -c "from apps.collector.ashare.main import app; print('OK')"`
Expected: OK

- [ ] **Step 3: commit**

```bash
git add apps/collector/ashare/bar_poller.py
git commit -m "feat(collector): A股 5m 收线启用 15m/30m 聚合(原直取改聚合)"
```

---

## Task 3: 美股 bar_poller 改 CORE 常驻 + 5m 聚合 15m/30m

**Files:**
- Modify: `apps/collector/us/bar_poller.py`

- [ ] **Step 1: _scan_symbols 改 CORE**

us/bar_poller.py 的 `_scan_symbols`(扫 `state:subscribe:us:*`)改为返回 CORE:
```python
from core.domain.core_symbols import CORE_SYMBOLS
async def _scan_symbols(self) -> set[str]:
    return set(CORE_SYMBOLS["us"])
```
直取周期 `_POLL_INTERVALS` 从 `("5m","15m","30m")` 改为 `("5m",)`。

- [ ] **Step 2: 5m 收线聚合加 15m/30m**

`poll_one` 中 `interval == "5m"` 触发处,targets 加 15m/30m:
```python
if interval == "5m":
    await aggregate_and_publish(self._repo, self._redis, "us", symbol,
        targets=("15m","30m","60m","4h"), now=datetime.now(timezone.utc))
```

- [ ] **Step 3: import 测试**

Run: `. .venv/bin/activate && python -c "from apps.collector.us.main import app; print('OK')"`
Expected: OK

- [ ] **Step 4: commit**

```bash
git add apps/collector/us/bar_poller.py
git commit -m "feat(collector): 美股 bar_poller 改 CORE 常驻, 5m 直取+聚合 15m/30m"
```

---

## Task 4: refill 加 CORE 白名单(前端不可触发名单外)

**Files:**
- Modify: `apps/api/routes/watchlists.py`(_refill_new_symbol)

- [ ] **Step 1: 改 _refill_new_symbol 加 CORE 校验**

```python
async def _refill_new_symbol(symbol: str, redis_cache, watchlist) -> None:
    from core.domain.core_symbols import core_symbols
    from core.domain.markets import infer_market
    mkt = infer_market(symbol)
    if symbol not in core_symbols(mkt):
        log.info("watchlist.refill_skip_non_core", symbol=symbol, market=mkt)
        return
    from apps.api.routes.symbols import _publish_refill_request
    days_map = {"5m": 30, "15m": 30, "30m": 60, "60m": 120, "4h": 365, "1d": 1825}
    for iv in ("5m", *SIGNAL_INTERVALS):
        try:
            await _publish_refill_request(redis_cache, symbol, iv, days_map.get(iv, 60), watchlist=watchlist)
        except Exception as e:  # noqa: BLE001
            log.warning("watchlist.refill_failed", symbol=symbol, interval=iv, error=str(e))
```

- [ ] **Step 2: import 测试**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; print('OK')"`
Expected: OK

- [ ] **Step 3: commit**

```bash
git add apps/api/routes/watchlists.py
git commit -m "fix(api): refill 加 CORE 白名单(前端不触发名单外标的采集)"
```

---

## Task 5: 重启验证 + 数据回填核对

- [ ] **Step 1: 重启 3 collector + api(雷区2 模板)**

```bash
pkill -9 -f "apps.collector.ashare.main"; pkill -9 -f "apps.collector.us.main"
pkill -9 -f "apps.collector.crypto.main"; pkill -9 -f "uvicorn apps.api.main:app"; sleep 2
nohup bash -c '. .venv/bin/activate && python -m apps.collector.ashare.main' >> /tmp/collector_ashare.log 2>&1 & disown
nohup bash -c '. .venv/bin/activate && python -m apps.collector.us.main' >> /tmp/collector_us.log 2>&1 & disown
nohup bash -c '. .venv/bin/activate && python -m apps.collector.crypto.main' >> /tmp/collector_crypto.log 2>&1 & disown
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 & disown
sleep 15
for p in 8787 8788 8789 8790; do lsof -nP -iTCP:$p -sTCP:LISTEN | grep -q LISTEN && echo "$p UP" || echo "$p DOWN"; done
```
Expected: 4 端口 UP

- [ ] **Step 2: 等聚合,核对 CORE 标的全周期入库**

```bash
sleep 60
. .venv/bin/activate && python3 -c "
import duckdb
con=duckdb.connect('data/bars_ashare.duckdb',read_only=True)
for iv in ['5m','15m','30m','60m','4h']:
    r=con.execute(f\"SELECT COUNT(DISTINCT symbol) FROM bars WHERE interval='{iv}' AND symbol IN ('600519.SH','300750.SZ')\").fetchone()[0]
    print(f'600519+300750 {iv}: {r}/2 有数据')
"
```
Expected: 各周期逐步补齐(5m 先到,15m/30m/60m/4h 随聚合补)

- [ ] **Step 3: 核对 600519 60m vs 富途口径(数据正确性)**

```bash
# 用之前验证脚本核对 60m 时间网格 10:30/11:30/14:00/15:00 + OHLC
```
Expected: 时间网格与富途一致

---

## 注意

- Task 2/3 的 `aggregate_and_publish` targets 传参:实施时先看其精确签名(`targets` 元组 vs `window_15m` kwargs),按实际签名传。若签名是 kwargs,用 `_INCR_DEFAULT`(195行 `window_15m=2`)。
- 1d 直取路径(A股/美股)本次不动,实施时确认未被破坏(Step2 核对含 1d)。
