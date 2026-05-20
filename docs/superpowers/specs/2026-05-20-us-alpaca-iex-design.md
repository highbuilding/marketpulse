# 美股数据接入 — Alpaca IEX 替换 akshare 路径

**日期**: 2026-05-20(同日修正)
**版本**: 1.0
**作者**: zhonghuai + Claude
**状态**: 待实施

---

## 0. 背景

2026-05-20 早些时候做了"akshare 1d 主源 + yfinance 备" 实施(`docs/superpowers/specs/2026-05-20-us-1d-akshare-fallback-design.md`),原因是当时认定 Alpaca free tier 不能拿历史 K 线。**实测发现这个判断错了**:Alpaca free IEX feed 实际可用,且覆盖 1d / 60m / 30m / 15m / 5m / 1m 全周期。错误根源是 SDK 用法 bug(`resp['SYMBOL']` 不能像 dict 用,要 `resp.data['SYMBOL']`)。

实测结果(commit `f2425ca` 之后用真 key 跑):

| 周期 | 历史窗口 | 数据量 | 最新 |
|---|---|---|---|
| 1d | 2020-07-27 → 2026-05-20 | 1462 根 | 5/20 收盘 |
| 60m | 最近 7 天 | 42 根 | 实时 |
| 30m / 15m / 5m / 1m | 最近 1-7 天 | 充足 | 实时(15min 延迟) |

因此:**Alpaca IEX 一站式覆盖美股完整需求**,akshare 路径推翻,K 线 + 完整 intraday + 4 个 CD 信号周期一并恢复。

---

## 1. 目标与非目标

### 1.1 目标

- **A. USAdapter 重构**:`fetch_history` 用 Alpaca IEX 主源 → yfinance 备(熔断保留);新增 `fetch_intraday` 走 Alpaca IEX
- **B. 移除 akshare 路径**:删 `_resolve_akshare_code` / `_fetch_history_akshare` / `_AKSHARE_PREFIXES`;`USAdapter.__init__` 删 `dir_repo` 参数;deps.py 删 `dir_repo` 注入
- **C. 前端 tab 完全恢复**:`klineTabsForMarket('us')` 恢复 1m/5m/15m/30m/60m/1d/1wk/1mo;`detailSignalTabs('us')` 恢复 15m/30m/60m/1d(**4h 仍排除**,见目标 D)
- **D. 4h 美股不显示**:对齐 A 股 / HK 口径(crypto 才显示 4h)。`klineTabsForMarket` / `detailSignalTabs` 都跳过 4h。原因:Alpaca IEX 60m 大部分时段没 prepost bar,4h 重采样会出残缺 bucket
- **E. CD 信号 cron 自动恢复**:`scheduler.py::attach_us_signal_jobs` 已存在(2026-05-18 spec 加的),除了删除 `cd:us:4h` cron(因为 4h 不再支持)
- **F. data/state.db 中 `akshare_code` 列保留**:SQLite ALTER 不能 DROP COLUMN,留着无害(NULL 列不占空间)
- **G. ts normalize**:1d ts 仍是"ET 自然交易日 00:00 → UTC"(雷区 3 对称);intraday ts 直接用 Alpaca 返回的 timestamp(已是 UTC)

### 1.2 非目标

- ❌ 不接 SIP 付费 feed($99/月)
- ❌ 不动 fund_flow / sectors / north_flow(美股本期不做资金流)
- ❌ 不动 watchlist 4-tab UI(2026-05-18 spec 已交付)
- ❌ 不删 `directory.akshare_code` 列(留 NULL,日后若再用 akshare 直接复用)
- ❌ 不动 backup_cb(yfinance 仍可能在异常情况下被打,熔断保留)
- ❌ 不动其他市场(A 股 / HK / Crypto)
- ❌ Alpaca historical 不加独立熔断器(直接 fallback yfinance,见 §3.3)

---

## 2. 数据源决策(修正后)

| 维度 | 决策 | 说明 |
|---|---|---|
| 实时 quote | **Alpaca latest_quote**(已通) | 已 work |
| **1d 历史 2020-至今** | **Alpaca IEX**(`StockBarsRequest, feed='iex'`) | 实测 1462 根 / 6 年 |
| Intraday 5m/15m/30m/60m | **Alpaca IEX** | 实测可用 |
| Intraday 1m | **Alpaca IEX** | 实测可用,15min 延迟 |
| 备份 | **yfinance**(circuit breaker 控制) | 仅 Alpaca 故障时启用 |
| 4h | **不支持** | IEX prepost 数据稀疏,4h 重采样会出残缺 bucket |

---

## 3. 架构改动面

| 层 | 文件 | 性质 |
|---|---|---|
| adapter | `core/adapters/us.py` | 重写 `fetch_history` + 新增 `fetch_intraday`(Alpaca);删除 akshare 相关 |
| api/deps | `apps/api/deps.py::get_kline_service` | 删 `adapters["us"].dir_repo = ...` |
| persistence | `core/persistence/symbol_directory_repo.py` | **不动**(`akshare_code` 列保留作 dead column) |
| scheduler | `core/scheduler/scheduler.py::attach_us_signal_jobs` | 删除 `cd:us:4h` job(其他保留) |
| web | `apps/web/lib/intervals.ts` | 恢复美股完整 K 线 tab + 信号 tab(4h 除外) |
| docs | `docs/TODO.md` | 删美股 intraday 未接入条目;加 SIP 升级备忘 |
| docs | `CLAUDE.md` | SSoT 表更新(akshare_code 列标 deprecated)|
| tests | `tests/unit/adapters/test_us.py` | 删 ~10 个 akshare 相关测试;加 ~6 个 Alpaca historical / intraday 测试 |

### 3.1 边界守则

- adapter 进出口 ticker 转换:`AAPL` → Alpaca 直接吃;`BRK.B` → Alpaca 接受 `BRK.B` 还是 `BRK-B` 待实测(默认按 `_to_yfinance_ticker(symbol)`,即 `BRK-B`,与 yfinance 路径一致)
- ts normalize:1d 走 ET 自然交易日 00:00 → UTC(雷区 3 对称);intraday 直接用 Alpaca timestamp(本就是 UTC)
- `feed='iex'` 必传,否则 SDK 默认走 SIP 被 free 账户拒绝

### 3.2 Alpaca 调用注意点(实测踩坑)

1. **`resp` 不是 dict**:`StockBarsRequest` 返回 `BarSet`,要用 `resp.data[symbol]`,不是 `resp[symbol]`
2. **`feed='iex'` 必填**:不传默认 SIP,free 账户报 `subscription does not permit querying recent SIP data`
3. **`end` 不要超 `now - 15min`**:免费 IEX 实时数据有 15min 延迟,query 超过这个窗口会返空(latest_quote / latest_bar 不受此限,因为是另一个端点)
4. **timezone**:Alpaca 返回 `timestamp` 是 timezone-aware UTC,直接用

### 3.3 yfinance backup 角色

- 仍保留 `backup_cb` 和 `_fetch_history_yfinance` / `_fetch_snapshot_yfinance`
- 触发场景:Alpaca 抛 APIError / network timeout 时
- 实际预期:**几乎不会触发**,因为 Alpaca 是 USAdapter 主路径,稳定

---

## 4. 详细设计

### 4.1 USAdapter 改造

#### `__init__`(简化)

```python
def __init__(self) -> None:
    self.api_key = os.getenv("ALPACA_API_KEY")
    self.secret = os.getenv("ALPACA_SECRET_KEY")
    self.has_primary = bool(self.api_key and self.secret)
    self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
    self.backup_cb = CircuitBreaker(fail_threshold=2, reset_after_s=1800)
    # 移除 self.dir_repo
```

#### `fetch_history`(主源切回 Alpaca)

```python
async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    """1d 历史。
    路径: Alpaca IEX 主源(免费, 完整) → yfinance 备(熔断保护)。
    """
    if self.has_primary:
        try:
            return await asyncio.to_thread(
                self._fetch_history_alpaca, symbol, start, end,
            )
        except Exception as e:
            log.warning("us.alpaca_history_failed", symbol=symbol, error=str(e))

    if not self.backup_cb.can_execute():
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
        raise AdapterError(...)
```

#### `_fetch_history_alpaca`(新)

```python
def _fetch_history_alpaca(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    """Alpaca IEX 拿 1d。
    ts normalize: Alpaca 返回的 timestamp 是 UTC 收盘时刻,
    我们改 normalize 到 ET 自然交易日 00:00 → UTC(雷区 3 对称)。
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(self.api_key, self.secret)
    yf_symbol = _to_yfinance_ticker(symbol)  # BRK.B → BRK-B
    # end 留 15 min 余量避开 IEX 实时延迟限制
    end_safe = end - timedelta(minutes=20) if (end - datetime.now(timezone.utc)).total_seconds() > -60 else end
    req = StockBarsRequest(
        symbol_or_symbols=yf_symbol,
        timeframe=TimeFrame.Day,
        start=start, end=end_safe, feed="iex",
    )
    resp = client.get_stock_bars(req)
    raw_bars = resp.data.get(yf_symbol, [])
    out: list[Bar] = []
    for b in raw_bars:
        # Alpaca 1d timestamp 是 UTC 04:00(EDT)/05:00(EST), 即 ET 自然日 00:00
        # 已经满足雷区 3 对称, 直接用
        ts_utc = b.timestamp
        out.append(Bar(
            market="us", symbol=symbol, ts=ts_utc,
            open=Decimal(str(float(b.open))),
            high=Decimal(str(float(b.high))),
            low=Decimal(str(float(b.low))),
            close=Decimal(str(float(b.close))),
            volume=int(b.volume) if b.volume else 0,
            interval="1d",
        ))
    return out
```

**关键观察**:Alpaca 1d 的 timestamp 已经是 ET 自然交易日 00:00 → UTC(EDT 时 04:00 UTC,EST 时 05:00 UTC)。这与雷区 3 对称约定**直接吻合**,不需要再 normalize。

#### `fetch_intraday`(改为 Alpaca)

```python
async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
    """Alpaca IEX intraday。
    freq: '1' / '5' / '15' / '30' / '60'。
    """
    interval_map = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "60m"}
    if freq not in interval_map:
        raise ValueError(f"unsupported freq: {freq}")
    if not self.has_primary:
        raise AdapterError("alpaca not configured for intraday", source="us")
    return await asyncio.to_thread(
        self._fetch_intraday_alpaca, symbol, freq,
    )

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
    # 1m: 7 天窗口; 其他: 60 天(对齐 spec 旧设计)
    days = 7 if freq == "1" else 60
    end_safe = datetime.now(timezone.utc) - timedelta(minutes=20)
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

`interval_map` 在 `fetch_intraday` 顶部已定义,这里 inline 用即可。

#### 删除项

- `_AKSHARE_PREFIXES` module-level 常量
- `_resolve_akshare_code` 方法
- `_fetch_history_akshare` 方法
- `from core.integrations.akshare import ak_call` import
- `from typing import TYPE_CHECKING` 块里的 `SymbolDirectoryRepo` import
- `__init__` 的 `dir_repo` 参数 + `self.dir_repo` 字段

### 4.2 deps.py

```python
@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    # 删除: adapters["us"].dir_repo = get_symbol_directory_repo()
    return KLineService(get_bar_repo(), adapters)
```

### 4.3 scheduler.py

`attach_us_signal_jobs` 删除 `cd:us:4h` job,其余 5 个 cron 保留(15m / 30m / 60m / 1d 自动恢复扫描)。

### 4.4 前端 intervals.ts

```typescript
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  // 4h 仅 crypto 显示;美股不再特殊限制
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isKline && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key, label: s.labelCn }))
}

export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isSignal && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
}
```

(撤销今天上午 Task 6 的"美股仅 1d/1wk/1mo"限制,完全恢复成 2026-05-18 spec 的版本,但 4h 对齐 A 股口径排除。)

### 4.5 测试改造

**删除测试**(akshare 不再用):
- `test_resolve_akshare_code_*`(5 个)
- `test_fetch_history_uses_akshare_when_resolved`
- `test_fetch_history_falls_back_to_yfinance_when_akshare_fails`
- `test_fetch_history_no_dir_repo_skips_to_yfinance`
- `test_us_adapter_accepts_dir_repo_optional`
- `test_fetch_history_yfinance_failure_records_backup_cb`(改造)
- `test_fetch_history_raises_when_yfinance_circuit_open`(改造)

**新增测试**(Alpaca):
- `test_fetch_history_uses_alpaca_when_configured`
- `test_fetch_history_alpaca_failure_falls_back_yfinance`
- `test_fetch_history_no_alpaca_falls_back_yfinance`
- `test_fetch_intraday_uses_alpaca`
- `test_fetch_intraday_alpaca_returns_proper_bars`
- `test_fetch_intraday_invalid_freq_raises`

**保留**:
- `test_us_adapter_has_backup_cb_with_strict_params`(yfinance backup_cb 仍在)
- `test_to_yfinance_ticker_*`(2 个,字符转换没变)
- `test_verify_ticker_*`(3 个,verify_ticker 仍在)
- `test_fetch_snapshot_*`(3 个,snapshot 没变)

---

## 5. 故障矩阵

| 故障 | 现象 | 处理 |
|---|---|---|
| Alpaca rate limit(超 200/min,极少) | APIError 429 | log warning + fallback yfinance |
| Alpaca 网络 timeout | timeout exception | log warning + fallback yfinance |
| 国内访问 Alpaca CDN 抖动 | 偶发失败 | 同上 |
| yfinance backup 也挂(罕见) | backup_cb 熔断 30 分钟 | UI 显示"加载失败" |
| Alpaca free 没拉到 IEX 数据(symbol 太冷门 IEX 不交易) | resp.data 空 list | 降级 yfinance(yfinance 通过 yahoo 数据,通常有) |
| 用户在 4h tab 上(已不显示) | UI tab 不渲染 | N/A(前端控制) |

---

## 6. 测试

### 6.1 单元测试(参见 §4.5)

### 6.2 集成测试(`@pytest.mark.integration`)

不加(本期 mock 已覆盖核心路径)。

### 6.3 手工验收

1. `curl /api/symbols/AAPL/bars?interval=1d&days=30` 返回 ≥ 20 条
2. `curl /api/symbols/AAPL/bars?interval=60m&days=7` 返回 ≥ 30 条
3. `curl /api/symbols/AAPL/bars?interval=5m&days=2` 返回 ≥ 100 条
4. 详情页 `/symbol/AAPL` 切 1d/60m/30m/15m/5m/1m K 线全部显示
5. 详情页 CD 信号面板 1d / 60m / 30m / 15m tab 显示数据(可能 0 条信号,但不是"加载失败")
6. 关注页美股 tab 添加 AAPL → 1d K 线显示

---

## 7. 回滚

- `core/adapters/us.py`:全段 revert 到 commit `f4f1379`(akshare 路径完整版本)
- `apps/web/lib/intervals.ts`:revert 到 Task 6(美股仅 1d/1wk/1mo)
- `apps/api/deps.py`:revert 注入 `dir_repo`
- 无 schema 改动需要回滚

---

## 8. 关键约束摘要(给实施者)

- Alpaca SDK 用 `resp.data[yf_symbol]`,**不是** `resp[yf_symbol]`
- `feed='iex'` 必传,否则免费账户被拒
- `end` 留 20 分钟余量(避开 IEX 15min 延迟)
- 1d ts 直接用 Alpaca timestamp(已是 ET 自然日 00:00 → UTC,雷区 3 满足)
- intraday ts 直接用 Alpaca timestamp(已是 UTC)
- `_to_yfinance_ticker(symbol)` 复用做 ticker 转换(BRK.B → BRK-B)
- 删除 dir_repo 注入路径(akshare_code 列保留作 dead column)
- 4h 美股不显示(对齐 A 股口径)
- 业务层 symbol 永远见 `BRK.B`,转换只在 adapter 进出口

---

## 9. 待办与未实施事项(写入 `docs/TODO.md`)

- 撤销今早登记的"美股 intraday 接入"段(已完成)
- 添加备忘:**SIP 付费升级**($99/月可获 2016 之前的全市场历史 + 实时 SIP feed),如果未来需要扩历史窗口或精度,这是路径
- 添加备忘:**Alpaca historical bars rate limit 监控**(目前 200/min 够用,但 watchlist 增长后需重新评估,并考虑 Alpaca historical 加专属 cb)
- `directory.akshare_code` 列保留 NULL,日后若再用 akshare 直接复用 schema
