# 美股 Alpaca IEX 替换 akshare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 USAdapter 美股 1d + intraday 主源切回 Alpaca IEX(免费实测可用),移除上午刚加的 akshare 路径,前端美股 K 线 tab 完整恢复(4h 仍排除,对齐 A 股口径)。

**Architecture:**
- USAdapter: Alpaca IEX 主源 → yfinance 备(熔断保留)
- `fetch_history` / `fetch_intraday` 都走 Alpaca
- 删除 akshare 相关:`_resolve_akshare_code` / `_fetch_history_akshare` / `_AKSHARE_PREFIXES` / `dir_repo` 字段
- 前端 `klineTabsForMarket` / `detailSignalTabs` 撤销美股仅 1d/1wk/1mo 限制
- scheduler 删 `cd:us:4h` job

**Tech Stack:** Python 3.11(alpaca-py 已安装、yfinance 备份、pandas、structlog、pytest);Next.js 14(TypeScript)

**Spec:** `docs/superpowers/specs/2026-05-20-us-alpaca-iex-design.md`

---

## File Structure

修改:
- `core/adapters/us.py` — 移除 akshare,新增 `_fetch_history_alpaca` / `_fetch_intraday_alpaca`,改 `fetch_history` / `fetch_intraday` 路径
- `apps/api/deps.py::get_kline_service` — 删 dir_repo 注入
- `core/scheduler/scheduler.py::attach_us_signal_jobs` — 删 cd:us:4h
- `apps/web/lib/intervals.ts` — 撤销美股 K 线/信号 tab 限制
- `tests/unit/adapters/test_us.py` — 删 akshare 测试 + 加 Alpaca 测试
- `docs/TODO.md` — 美股 intraday 已接入,加 SIP 备忘
- `CLAUDE.md` — SSoT 表 akshare_code 标 deprecated

---

## Task 1: USAdapter 移除 akshare 路径 + 接 Alpaca historical

**Files:**
- Modify: `core/adapters/us.py`(移除 akshare 相关 + 新增 `_fetch_history_alpaca` + 改 `fetch_history` 主源)
- Modify: `tests/unit/adapters/test_us.py`(删 ~10 个 akshare 测试 + 加 3 个 Alpaca historical 测试)

- [ ] **Step 1: 删 akshare 相关测试**

读 `tests/unit/adapters/test_us.py`,删除以下测试函数(整段):

- `test_resolve_akshare_code_cached`
- `test_resolve_akshare_code_probes_105_first`
- `test_resolve_akshare_code_falls_back_106`
- `test_resolve_akshare_code_all_fail_returns_none`
- `test_resolve_akshare_code_no_repo_returns_none`
- `test_us_adapter_accepts_dir_repo_optional`
- `test_fetch_history_uses_akshare_when_resolved`
- `test_fetch_history_falls_back_to_yfinance_when_akshare_fails`
- `test_fetch_history_raises_when_yfinance_circuit_open`
- `test_fetch_history_yfinance_failure_records_backup_cb`
- `test_fetch_history_no_dir_repo_skips_to_yfinance`

剩下保留:`_to_yfinance_ticker_*`(2)、`_mock_intraday_df` / `_mock_history_df`(辅助)、`fetch_intraday_*`(5)、`fetch_history_normalizes_to_et_midnight`、`fetch_history_class_share`、`fetch_history_winter_est_offset`、`verify_ticker_*`(4)、`fetch_snapshot_skips_yfinance_when_circuit_open`、`us_adapter_has_backup_cb_with_strict_params`。

- [ ] **Step 2: 写新 Alpaca historical 测试**

把以下追加到测试文件末尾:

```python
def _mock_alpaca_bar(timestamp, open_, high, low, close, volume):
    """模拟 Alpaca SDK 返回的 Bar 对象。"""
    bar = MagicMock()
    bar.timestamp = timestamp
    bar.open = open_
    bar.high = high
    bar.low = low
    bar.close = close
    bar.volume = volume
    return bar


@pytest.mark.asyncio
async def test_fetch_history_uses_alpaca_when_configured():
    """has_primary=True → 走 Alpaca, 不调 yfinance。"""
    adapter = USAdapter()
    adapter.has_primary = True  # 强制 (mock 环境无 key)
    fake_bars = [
        _mock_alpaca_bar(
            datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc),
            296.97, 300.51, 296.35, 298.97, 42243561,
        ),
    ]
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": fake_bars}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client), \
         patch("core.adapters.us.yf") as mock_yf:
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
    assert bars[0].market == "us"
    assert bars[0].interval == "1d"
    assert bars[0].ts == datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc)
    assert bars[0].close == Decimal("298.97")
    mock_yf.download.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_history_alpaca_failure_falls_back_yfinance():
    """Alpaca 抛 → fallback yfinance(backup_cb 未熔断时)。"""
    adapter = USAdapter()
    adapter.has_primary = True
    yf_df = _mock_history_df()
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               side_effect=RuntimeError("alpaca network")), \
         patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=yf_df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2  # _mock_history_df 返 2 行
    mock_yf.download.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_history_no_alpaca_falls_back_yfinance():
    """has_primary=False(无 key)→ 直接 yfinance。"""
    adapter = USAdapter()
    adapter.has_primary = False
    yf_df = _mock_history_df()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=yf_df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    mock_yf.download.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_history_class_share_uses_dash():
    """BRK.B → Alpaca 拿 BRK-B, Bar.symbol 仍 BRK.B。"""
    adapter = USAdapter()
    adapter.has_primary = True
    fake_bars = [_mock_alpaca_bar(
        datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc),
        500.0, 510.0, 495.0, 505.0, 1000000,
    )]
    fake_resp = MagicMock()
    fake_resp.data = {"BRK-B": fake_bars}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        bars = await adapter.fetch_history(
            "BRK.B",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert bars[0].symbol == "BRK.B"
    # 验证 Alpaca 收到 BRK-B
    call = fake_client.get_stock_bars.call_args
    assert call.args[0].symbol_or_symbols == "BRK-B"
```

(保留 `test_fetch_history_normalizes_to_et_midnight` / `test_fetch_history_class_share` / `test_fetch_history_winter_est_offset` 这三个原 yfinance 测试 — 它们测的是 yfinance 路径,而 yfinance 走 fallback 仍然存在)

但 `test_fetch_history_normalizes_to_et_midnight` 现在 has_primary=True 时不会走 yfinance,需要改为先 disable primary:

```python
@pytest.mark.asyncio
async def test_fetch_history_normalizes_to_et_midnight():
    """yfinance fallback 路径 1d ts 必须 normalize 为 ET 自然交易日 00:00 → UTC。
    2026-05-15 00:00 ET (EDT, UTC-4) → 2026-05-15 04:00 UTC。"""
    adapter = USAdapter()
    adapter.has_primary = False  # 强制走 yfinance
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_history_df())
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    assert bars[0].ts == datetime(2026, 5, 15, 4, 0, tzinfo=timezone.utc)
    assert bars[1].ts == datetime(2026, 5, 16, 4, 0, tzinfo=timezone.utc)
    assert bars[0].interval == "1d"
    assert bars[0].market == "us"
```

类似地 `test_fetch_history_class_share` 和 `test_fetch_history_winter_est_offset` 都加 `adapter.has_primary = False`。

- [ ] **Step 3: 改 `core/adapters/us.py`**

完整改动:

**a)** 顶部 imports:
- 删 `from core.integrations.akshare import ak_call`
- 删 `if TYPE_CHECKING: from core.persistence.symbol_directory_repo import SymbolDirectoryRepo`
- 删 `from typing import TYPE_CHECKING, Callable`,改回 `from typing import Callable`(如果不再用 TYPE_CHECKING)
- 保留 `from datetime import datetime, timezone, timedelta`(timedelta 仍要用,end_safe 计算)

**b)** 删除 module-level 常量 `_AKSHARE_PREFIXES = (...)`

**c)** `__init__` 简化(删 dir_repo):
```python
def __init__(self) -> None:
    self.api_key = os.getenv("ALPACA_API_KEY")
    self.secret = os.getenv("ALPACA_SECRET_KEY")
    self.has_primary = bool(self.api_key and self.secret)
    self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
    self.backup_cb = CircuitBreaker(fail_threshold=2, reset_after_s=1800)
```

**d)** 删除方法:
- `_resolve_akshare_code`(整段)
- `_fetch_history_akshare`(整段)

**e)** 重写 `fetch_history`:
```python
async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    """1d 历史。
    路径: Alpaca IEX 主源 → yfinance 备(熔断保护)。
    """
    if self.has_primary:
        try:
            return await asyncio.to_thread(
                self._fetch_history_alpaca, symbol, start, end,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("us.alpaca_history_failed",
                        symbol=symbol, error=str(e))

    if not self.backup_cb.can_execute():
        log.warning("us.yfinance_circuit_open_skip_history", symbol=symbol)
        raise AdapterError(
            f"alpaca unavailable and yfinance circuit open for {symbol}",
            source="us",
        )
    try:
        bars = await self._fetch_history_yfinance(symbol, start, end)
        self.backup_cb.record_success()
        return bars
    except Exception as e:
        self.backup_cb.record_failure()
        raise AdapterError(
            f"both alpaca and yfinance failed for {symbol}: {e}",
            source="us",
        ) from e
```

**f)** 新加 `_fetch_history_alpaca`(在 `_fetch_history_yfinance` 之前):
```python
def _fetch_history_alpaca(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    """Alpaca IEX 拿 1d。
    Alpaca 1d timestamp 已是 ET 自然交易日 00:00 → UTC, 满足雷区 3 对称。
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(self.api_key, self.secret)
    yf_symbol = _to_yfinance_ticker(symbol)
    # IEX 实时数据有 15 min 延迟, end 留 20 min 余量
    now = datetime.now(timezone.utc)
    end_safe = min(end, now - timedelta(minutes=20))
    req = StockBarsRequest(
        symbol_or_symbols=yf_symbol,
        timeframe=TimeFrame.Day,
        start=start, end=end_safe, feed="iex",
    )
    resp = client.get_stock_bars(req)
    raw_bars = resp.data.get(yf_symbol, [])
    out: list[Bar] = []
    for b in raw_bars:
        out.append(Bar(
            market="us", symbol=symbol, ts=b.timestamp,
            open=Decimal(str(float(b.open))),
            high=Decimal(str(float(b.high))),
            low=Decimal(str(float(b.low))),
            close=Decimal(str(float(b.close))),
            volume=int(b.volume) if b.volume else 0,
            interval="1d",
        ))
    return out
```

(`_fetch_history_yfinance` 保留不动)

- [ ] **Step 4: 跑测试**

```bash
cd /Users/xiangrong/stock/marketpulse
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v 2>&1 | tail -50
```
Expected: 删除 11 个 + 加 3 个 + 改 3 个 = 总数减少 ~8 个。重点 `test_fetch_history_uses_alpaca_when_configured` / `test_fetch_history_alpaca_failure_falls_back_yfinance` / `test_fetch_history_no_alpaca_falls_back_yfinance` / `test_fetch_history_class_share_uses_dash` 都过。

- [ ] **Step 5: import smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): fetch_history 切回 Alpaca IEX 主源, 移除 akshare 路径

- 删 _resolve_akshare_code / _fetch_history_akshare / _AKSHARE_PREFIXES
- __init__ 移除 dir_repo 参数(akshare 不再用)
- 新增 _fetch_history_alpaca 走 alpaca-py StockBarsRequest(feed='iex')
- yfinance 备份路径保留, backup_cb 熔断仍生效
- 实测: AAPL 1d 2020-至今 1462 根, BRK.B → BRK-B 转换 OK"
```

---

## Task 2: USAdapter 接 Alpaca intraday(替换 yfinance intraday)

**Files:**
- Modify: `core/adapters/us.py`(改 `fetch_intraday` 走 Alpaca + 新加 `_fetch_intraday_alpaca`)
- Modify: `tests/unit/adapters/test_us.py`(改/加 intraday 测试)

- [ ] **Step 1: 加 Alpaca intraday 测试**

把以下追加到测试文件末尾:

```python
@pytest.mark.asyncio
async def test_fetch_intraday_uses_alpaca():
    """has_primary=True → fetch_intraday 走 Alpaca。"""
    adapter = USAdapter()
    adapter.has_primary = True
    fake_bars = [
        _mock_alpaca_bar(
            datetime(2026, 5, 20, 13, 30, tzinfo=timezone.utc),
            300.0, 301.0, 299.5, 300.8, 50000,
        ),
        _mock_alpaca_bar(
            datetime(2026, 5, 20, 14, 30, tzinfo=timezone.utc),
            300.8, 302.0, 300.5, 301.5, 60000,
        ),
    ]
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": fake_bars}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        bars = await adapter.fetch_intraday("AAPL", freq="60")
    assert len(bars) == 2
    assert bars[0].interval == "60m"
    assert bars[0].ts == datetime(2026, 5, 20, 13, 30, tzinfo=timezone.utc)
    assert bars[1].close == Decimal("301.5")


@pytest.mark.asyncio
async def test_fetch_intraday_alpaca_5m_freq():
    """5m freq 调用 TimeFrame(5, Minute)。"""
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    adapter = USAdapter()
    adapter.has_primary = True
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": []}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        await adapter.fetch_intraday("AAPL", freq="5")
    call = fake_client.get_stock_bars.call_args
    tf = call.args[0].timeframe
    assert tf.amount == 5
    assert tf.unit == TimeFrameUnit.Minute


@pytest.mark.asyncio
async def test_fetch_intraday_invalid_freq_raises():
    adapter = USAdapter()
    with pytest.raises(ValueError, match="unsupported freq"):
        await adapter.fetch_intraday("AAPL", freq="2")


@pytest.mark.asyncio
async def test_fetch_intraday_no_alpaca_raises():
    """has_primary=False → 抛 AdapterError(intraday 不 fallback yfinance)。"""
    from core.adapters.base import AdapterError
    adapter = USAdapter()
    adapter.has_primary = False
    with pytest.raises(AdapterError, match="alpaca not configured"):
        await adapter.fetch_intraday("AAPL", freq="60")
```

- [ ] **Step 2: 删除现有 yfinance intraday 测试**

删除以下测试(因为 fetch_intraday 不再走 yfinance):
- `test_fetch_intraday_basic`
- `test_fetch_intraday_class_share_converts_ticker`
- `test_fetch_intraday_drops_nan`
- `test_fetch_intraday_drops_high_low_nan`
- `test_fetch_intraday_period_mapping`

(`_mock_intraday_df` 辅助函数也可删,如不再被引用 — 用 grep 确认)

- [ ] **Step 3: 改 `core/adapters/us.py::fetch_intraday`**

把整段 `async def fetch_intraday` 替换为:

```python
async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
    """Alpaca IEX intraday。freq: '1' / '5' / '15' / '30' / '60'。
    1m 限 7 天历史(IEX delay 15 min);其他 60 天。
    """
    if freq not in ("1", "5", "15", "30", "60"):
        raise ValueError(f"unsupported freq: {freq}")
    if not self.has_primary:
        raise AdapterError("alpaca not configured for intraday", source="us")
    return await asyncio.to_thread(self._fetch_intraday_alpaca, symbol, freq)


def _fetch_intraday_alpaca(self, symbol: str, freq: str) -> list[Bar]:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    tf_map = {
        "1":  TimeFrame.Minute,
        "5":  TimeFrame(5, TimeFrameUnit.Minute),
        "15": TimeFrame(15, TimeFrameUnit.Minute),
        "30": TimeFrame(30, TimeFrameUnit.Minute),
        "60": TimeFrame.Hour,
    }
    interval_map = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "60m"}
    days = 7 if freq == "1" else 60
    now = datetime.now(timezone.utc)
    end_safe = now - timedelta(minutes=20)
    start = end_safe - timedelta(days=days)

    client = StockHistoricalDataClient(self.api_key, self.secret)
    yf_symbol = _to_yfinance_ticker(symbol)
    req = StockBarsRequest(
        symbol_or_symbols=yf_symbol,
        timeframe=tf_map[freq],
        start=start, end=end_safe, feed="iex",
    )
    resp = client.get_stock_bars(req)
    raw_bars = resp.data.get(yf_symbol, [])
    out: list[Bar] = []
    interval = interval_map[freq]
    for b in raw_bars:
        out.append(Bar(
            market="us", symbol=symbol, ts=b.timestamp,
            open=Decimal(str(float(b.open))),
            high=Decimal(str(float(b.high))),
            low=Decimal(str(float(b.low))),
            close=Decimal(str(float(b.close))),
            volume=int(b.volume) if b.volume else 0,
            interval=interval,
        ))
    return out
```

(`pd` import 现在仅用于 `_fetch_history_yfinance` 的 NaN 检查,保留不动)

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v 2>&1 | tail -40
```

Expected: intraday 4 个新测试 pass。原有删除 5 个,改造 0 个。

- [ ] **Step 5: import smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```

- [ ] **Step 6: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): fetch_intraday 改走 Alpaca IEX(支持 1m/5m/15m/30m/60m)"
```

---

## Task 3: deps.py 删 dir_repo 注入 + scheduler 删 cd:us:4h

**Files:**
- Modify: `apps/api/deps.py::get_kline_service`
- Modify: `core/scheduler/scheduler.py::attach_us_signal_jobs`

- [ ] **Step 1: 改 `apps/api/deps.py::get_kline_service`**

找到现有实现:

```python
@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    if "us" in adapters:
        adapters["us"].dir_repo = get_symbol_directory_repo()
    return KLineService(get_bar_repo(), adapters)
```

把 `if "us" in adapters: adapters["us"].dir_repo = get_symbol_directory_repo()` 这两行删除:

```python
@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    return KLineService(get_bar_repo(), adapters)
```

- [ ] **Step 2: 改 `core/scheduler/scheduler.py::attach_us_signal_jobs`**

读 `attach_us_signal_jobs` 函数,找到 `cd:us:4h` job 那段(应该有 5 个 sched.add_job),整段删掉:

```python
    # 4h: 收盘点 ET 08:00 / 12:00 / 16:00 / 20:00, 各 +5
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="8,12,16,20", minute="5", timezone=et),
        id="cd:us:4h",
        kwargs={"interval": "4h", "market_filter": "us"},
        **common,
    )
```

剩下 15m / 30m / 60m / 1d 4 个 job。

- [ ] **Step 3: 后端 import smoke + 启动验证**

```bash
cd /Users/xiangrong/stock/marketpulse
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null; sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 8
grep -E "scheduler.us_signal_jobs_attached|Application startup complete" data/logs/api.log | tail -3
grep -c FATAL data/logs/api.log
# 验证 us adapter dir_repo 不再注入
. .venv/bin/activate && python -c "
from apps.api.deps import get_kline_service
us = get_kline_service().adapters['us']
assert not hasattr(us, 'dir_repo') or us.dir_repo is None, f'dir_repo still set: {us.dir_repo}'
print('NO_DIR_REPO_OK')
"
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```

Expected:
- `Application startup complete` ✓
- `scheduler.us_signal_jobs_attached` ✓
- FATAL=0
- `NO_DIR_REPO_OK`

- [ ] **Step 4: Commit**

```bash
git add apps/api/deps.py core/scheduler/scheduler.py
git commit -m "refactor(us): 删 dir_repo 注入 + cd:us:4h scheduler job(akshare 不再用)"
```

---

## Task 4: 前端 K 线/信号 tab 完全恢复

**Files:**
- Modify: `apps/web/lib/intervals.ts`(撤销美股仅 1d/1wk/1mo 限制)

- [ ] **Step 1: 改 `klineTabsForMarket` + `detailSignalTabs`**

读 `apps/web/lib/intervals.ts`,把:

```typescript
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  // 美股 intraday 暂未接入(yfinance ban + akshare 长窗口拉不动),仅 1d/1wk/1mo
  if (market === 'us') {
    return INTERVAL_SPECS
      .filter((s) => s.isKline && ['1d', '1wk', '1mo'].includes(s.key))
      .map((s) => ({ key: s.key, label: s.labelCn }))
  }
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isKline && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key, label: s.labelCn }))
}
```

整段替换为(撤销美股特殊分支):

```typescript
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  // 4h 仅 crypto 显示(美股 4h 重采样会出残缺 bucket;A 股/HK 4h ≡ 1d 无意义)
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isKline && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key, label: s.labelCn }))
}
```

类似地,把 `detailSignalTabs` 整段:

```typescript
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  // 美股仅 1d 信号(intraday 暂未接入)
  if (market === 'us') {
    return INTERVAL_SPECS
      .filter((s) => s.isSignal && s.key === '1d')
      .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
  }
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isSignal && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
}
```

替换为:

```typescript
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  // 4h 仅 crypto 显示
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isSignal && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
}
```

- [ ] **Step 2: tsc**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0

- [ ] **Step 3: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/lib/intervals.ts
git commit -m "feat(web): 美股 K 线/信号 tab 完整恢复(Alpaca IEX 接入完整 intraday)"
```

---

## Task 5: 端到端冒烟 + 文档更新

**Files:**
- Modify: `docs/TODO.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 端到端测试美股 K 线全周期**

```bash
cd /Users/xiangrong/stock/marketpulse
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null; sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 8

# 验证美股 1d 走 Alpaca
echo "=== AAPL 1d (近 30 天) ==="
curl -s -m 15 "http://localhost:8787/api/symbols/AAPL/bars?interval=1d&days=30" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
print('bars:', len(d.get('bars', [])))
if d.get('bars'):
    print('  first ts:', d['bars'][0]['ts'], 'close:', d['bars'][0]['close'])
    print('  last ts: ', d['bars'][-1]['ts'], 'close:', d['bars'][-1]['close'])
"

# 验证美股 60m
echo "=== AAPL 60m (近 7 天) ==="
curl -s -m 15 "http://localhost:8787/api/symbols/AAPL/bars?interval=60m&days=7" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
print('bars:', len(d.get('bars', [])))
"

# 验证美股 5m
echo "=== AAPL 5m (近 2 天) ==="
curl -s -m 15 "http://localhost:8787/api/symbols/AAPL/bars?interval=5m&days=2" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
print('bars:', len(d.get('bars', [])))
"

# 验证美股 15m
echo "=== AAPL 15m (近 7 天) ==="
curl -s -m 15 "http://localhost:8787/api/symbols/AAPL/bars?interval=15m&days=7" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
print('bars:', len(d.get('bars', [])))
"

# 失败计数
echo "=== alpaca / yfinance 失败计数 ==="
grep -c "us.alpaca_history_failed" data/logs/api.log
grep -c "us.yfinance_circuit_open" data/logs/api.log
echo "--- FATAL ---"
grep -c FATAL data/logs/api.log

pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```

Expected:
- 1d ≥ 20 条;60m ≥ 30 条;5m ≥ 100 条;15m ≥ 50 条
- Alpaca / yfinance 失败计数 0
- FATAL = 0

- [ ] **Step 2: 改 `docs/TODO.md`**

读现有文件,**删除**今天上午加的"美股 intraday 接入(2026-05-20 spec)"整节。

在末尾**追加**新备忘:

```markdown

## 美股 Alpaca IEX 已接入(2026-05-20 spec 修订)

后端美股数据全部走 Alpaca IEX 主源(免费, 实测 1d 2020-至今, intraday 5m/15m/30m/60m 60 天历史, 1m 7 天)。

后续可选优化:
- **SIP 付费升级**($99/月):获 2016 之前的全市场历史 + 实时 SIP feed(无 15min 延迟)。如需扩历史窗口或精度
- Alpaca historical bars rate limit 监控:目前 200/min 够用;watchlist 增长后需重新评估
- `directory.akshare_code` 列保留作 dead column,日后若再用 akshare 直接复用 schema
- 美股 4h 暂不支持(Alpaca IEX prepost bar 稀疏,4h 重采样残缺);对齐 A 股 / HK 口径
```

- [ ] **Step 3: 改 `CLAUDE.md`**

如果"规范 1:单一事实源(SSoT)收口表"中之前加了 `akshare_code` 相关的条目,加一行注释标 deprecated。

读 `CLAUDE.md` 确认是否有相关项,如有则改;如无则跳过。

最少改动:在"当前活跃约束"小节末尾(如有)追加:

```markdown
- 美股数据源 2026-05-20 切回 Alpaca IEX(免费层,完整支持 1d + 1m/5m/15m/30m/60m intraday);上午接入的 akshare 路径已删除,但 `directory.akshare_code` 列保留作 dead column
```

如果"当前活跃约束"小节的"美股 4h tab" 描述还在(2026-05-18 spec 加的),保留即可(意思仍然成立)。

- [ ] **Step 4: 全量回归 + Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
. .venv/bin/activate && pytest tests/unit/ -v 2>&1 | tail -10
cd apps/web && npx tsc --noEmit
echo "tsc exit=$?"
cd /Users/xiangrong/stock/marketpulse
git add docs/TODO.md CLAUDE.md
git commit -m "docs: 美股 Alpaca IEX 接入完成, 更新 TODO + CLAUDE.md"
```

---

## Self-Review

**Spec 覆盖**:
- §1.1 A USAdapter 重构 → Task 1 + Task 2 ✓
- §1.1 B 移除 akshare → Task 1 + Task 3(deps) ✓
- §1.1 C 前端 tab 恢复 → Task 4 ✓
- §1.1 D 4h 美股不显示 → Task 4(`klineTabsForMarket` / `detailSignalTabs` 仅 crypto) + Task 3(scheduler 删 cd:us:4h) ✓
- §1.1 E CD 信号 cron 自动恢复 → Task 3 + 自动启用 15m/30m/60m/1d ✓
- §1.1 F akshare_code 列保留 → 不动(无 task 需求)✓
- §1.1 G ts normalize → Task 1 `_fetch_history_alpaca` 直接用 b.timestamp(已是雷区 3 对称) ✓

**Placeholder 扫描**:无 TBD/TODO,每步均含完整代码 + 验证命令。

**类型一致性**:
- `_fetch_history_alpaca(symbol, start, end) -> list[Bar]`:Task 1 / Task 5 e2e 测试一致
- `_fetch_intraday_alpaca(symbol, freq) -> list[Bar]`:Task 2 / Task 5 一致
- `_to_yfinance_ticker` 仍是 module-level 函数,Task 1 / Task 2 调用一致

**风险点(实施时格外注意)**:
- Task 1 删测试时不能误删 fetch_history yfinance 路径的测试(那些必须保留作 fallback 验证)
- Task 1 改 `__init__` 时 dir_repo 参数完全删除,签名变化 — 但实测目前没 caller 传 dir_repo(Task 5 deps 注入也即将删除),所以兼容
- Task 3 `dir_repo` 注入只在 deps 那一处,删干净就行
- Task 4 前端撤销限制后,`detailSignalTabs(null)` 调用(从老的临时改 Task 13 留下)行为也会变(从仅 4 个变成 4-5 个,但 4h 仍排除),这是恢复正常,不是回归
