# 美股 K 线降级与 akshare 1d 主源接入设计

**日期**: 2026-05-20
**版本**: 1.0
**作者**: zhonghuai + Claude
**状态**: 待实施

---

## 0. 背景

2026-05-20 测试发现"美股盘的数据加载失败":

1. **yfinance 被 IP-level rate limit / connection ban**(美东时间高峰 + 日内多次 fetch_history 后触发,持续数小时)
2. **Alpaca free tier 不含历史 K 线**:Latest quote OK,但 historical bars API 返回 ~30 天 IEX feed,不够支撑 spec 要求的 2020-至今
3. 原 `USAdapter._fetch_snapshot_yfinance` 在 backup 路径**无 CircuitBreaker**,被 ban 后仍每 10 秒 hammer yfinance,延长封禁

因此美股 K 线 / CD 信号不可用。本 spec 解决"K 线 / 1d CD 信号 立即恢复",intraday 接入推迟。

---

## 1. 目标与非目标

### 1.1 目标

- **A1 yfinance 熔断硬化**:`USAdapter` 给 yfinance backup 路径加独立 CircuitBreaker(`fail_threshold=2, reset_after_s=1800`),429 / 网络失败 2 次后熔断 30 分钟
- **A2 akshare 1d 主源**:加 `_fetch_history_akshare` 路径,优先级 **akshare 1d → yfinance 1d(熔断时跳过)**
- **ticker → akshare code 映射**:首次试 `105.X` / `106.X` / `107.X` 三种前缀,成功后回写 `symbol_directory.akshare_code` 列,后续命中缓存
- **schema 自动升级**:`SymbolDirectoryRepo._connect` 时检测列是否存在,缺则 `ALTER TABLE ADD COLUMN akshare_code TEXT NULL`,幂等
- **1d ts 对齐**:akshare 返回的 `'2026-05-19'` 字符串通过 `tz_localize('America/New_York').tz_convert('UTC')` normalize,与现有 yfinance 1d 路径完全等价(雷区 3 对称)
- **UI 文案**:美股详情页 intraday tab 显示"美股 intraday 暂未接入"提示

### 1.2 非目标

- ❌ 美股 intraday(15m/30m/60m/4h)接入 — akshare 仅有 1m 且长窗口拉不动,本期跳过
- ❌ 美股 4h K 线 / 4h CD 信号 — 依赖 intraday
- ❌ akshare `stock_us_spot_em` 接入 — 全量分页拉不动,Alpaca latest quote 已通,不替换
- ❌ Alpaca 历史 K 线付费升级
- ❌ 富途 SDK / 其他备用源 — 留作后续 Plan B
- ❌ 美股 directory `_US_SEEDS` 预热 akshare_code — 静态 149 ticker 不预热,用户首次访问时 lazy 试探(YAGNI:绝大多数 ticker 用不到)

---

## 2. 数据源决策

| 维度 | 决策 | 备选 |
|---|---|---|
| 实时 quote | **Alpaca latest quote**(已通) | yfinance 弃用 |
| **1d 历史(2020-至今)** | **akshare `stock_us_hist`** | yfinance 1d 仅作熔断恢复期备份 |
| Intraday(15m/30m/60m) | **不接入** | 显示 UI 提示 |
| Intraday 1m | **不接入** | 同上 |

**关键约束**:akshare 走 mini_racer 雷区 1,但已被 `core/integrations/akshare.py::ak_call` 全局锁罩住,A 股一直在用,稳定。

---

## 3. 架构改动面

| 层 | 文件 | 性质 |
|---|---|---|
| persistence | `core/persistence/symbol_directory_repo.py` | `_connect` 内幂等 ALTER TABLE 加 `akshare_code`;新增 `get_akshare_code` / `set_akshare_code` 方法 |
| service | `core/services/symbol_directory_service.py` | 新增 `get_akshare_code` / `set_akshare_code` 透传方法 |
| adapter | `core/adapters/us.py` | 加 `backup_cb` 字段(独立 CircuitBreaker for yfinance);改 `fetch_history` 为"akshare 主 → yfinance 备" 路径;新增 `_fetch_history_akshare`;`_fetch_snapshot_yfinance` 也走 backup_cb |
| adapter | `core/adapters/us.py` | 加 `_resolve_akshare_code(symbol, dir_repo)` 助手函数,首次 105/106/107 试探 + 回写 |
| api/deps | `apps/api/deps.py` | `USAdapter()` 构造接 `dir_repo`(注入,让 adapter 能查/写 akshare_code) |
| api/routes | `apps/api/routes/symbols.py::bars` | 失败时返回明确错误码 + 错误文案,前端可识别 |
| web | `apps/web/components/KLineChart.tsx` | 加载失败时显示"美股 intraday 暂未接入,请看日线"提示(仅 us market + intraday) |
| web | `apps/web/lib/intervals.ts::klineTabsForMarket` | 美股暂时只显示 1d/1wk/1mo + 1m 不显示;15m/30m/60m/4h **从美股 tab 列表删除** |

### 3.1 边界守则

- `_resolve_akshare_code` 在 adapter 层,**不在 service**(因为只有 adapter 关心 yfinance 格式 / akshare 格式转换,业务层永远见 `AAPL`)
- yfinance backup_cb 与 Alpaca primary_cb **独立**,各自计数
- akshare 路径走 `ak_call`,自动加锁(雷区 1)

---

## 4. 详细设计

### 4.1 Schema 自动升级

`SymbolDirectoryRepo` 顶部加幂等检查:

```python
class SymbolDirectoryRepo:
    _schema_ensured = False

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if not SymbolDirectoryRepo._schema_ensured:
                await self._ensure_schema(db)
                SymbolDirectoryRepo._schema_ensured = True
            yield db

    @staticmethod
    async def _ensure_schema(db) -> None:
        cur = await db.execute("PRAGMA table_info(symbol_directory)")
        cols = {r[1] for r in await cur.fetchall()}
        if "akshare_code" not in cols:
            await db.execute(
                "ALTER TABLE symbol_directory ADD COLUMN akshare_code TEXT"
            )
            await db.commit()
```

**注意**:`_schema_ensured` 是类级 flag(不是实例级),保证多实例情况下也只 ALTER 一次。

新方法:

```python
async def get_akshare_code(self, symbol: str) -> str | None:
    async with self._connect() as db:
        cur = await db.execute(
            "SELECT akshare_code FROM symbol_directory WHERE symbol = ?",
            (symbol,),
        )
        row = await cur.fetchone()
    return row["akshare_code"] if row and row["akshare_code"] else None

async def set_akshare_code(self, symbol: str, code: str) -> None:
    """设置 akshare_code(symbol 必须已在 directory 中,只更新此列)。"""
    now = datetime.now(timezone.utc).isoformat()
    async with self._connect() as db:
        await db.execute(
            "UPDATE symbol_directory SET akshare_code = ?, updated_at = ? "
            "WHERE symbol = ?",
            (code, now, symbol),
        )
        await db.commit()
```

### 4.2 USAdapter Circuit Breaker 拆分

```python
class USAdapter:
    def __init__(self, dir_repo: SymbolDirectoryRepo | None = None) -> None:
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_SECRET_KEY")
        self.has_primary = bool(self.api_key and self.secret)
        # 原:primary_cb 给 Alpaca
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
        # 新:backup_cb 给 yfinance, 更激进
        self.backup_cb = CircuitBreaker(fail_threshold=2, reset_after_s=1800)
        # 新:dir_repo 用于 akshare_code 缓存
        self.dir_repo = dir_repo
```

`fetch_snapshot` 修改:

```python
async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
    if self.has_primary and self.primary_cb.can_execute():
        try:
            quotes = await asyncio.to_thread(self._fetch_snapshot_alpaca, symbols)
            self.primary_cb.record_success()
            return quotes
        except Exception as e:
            self.primary_cb.record_failure()
            log.warning("us.alpaca_failed", error=str(e))
    # yfinance backup 也要过 circuit breaker
    if not self.backup_cb.can_execute():
        log.debug("us.yfinance_circuit_open")
        return []  # 静默返空, 不抛异常(snapshot 是 best-effort)
    try:
        quotes = await asyncio.to_thread(self._fetch_snapshot_yfinance, symbols)
        self.backup_cb.record_success()
        return quotes
    except Exception as e:
        self.backup_cb.record_failure()
        raise AdapterError(f"both primary and backup failed: {e}", source="us") from e
```

**注意**:`_fetch_snapshot_yfinance` 内部循环每 symbol 单调,失败也只记日志不抛 — 改成"如果整批 0 success 才算 backup_cb failure"过严,改成"整批 zero quote 时 record_failure":

```python
def _fetch_snapshot_yfinance(self, symbols: list[str]) -> list[Quote]:
    # ... 原循环 ...
    if not out and symbols:  # 新增: 整批失败视作 backup 故障
        raise RuntimeError("yfinance returned 0 quotes for all symbols")
    return out
```

(让外层 except 捕获,触发 backup_cb.record_failure())

### 4.3 fetch_history "akshare 主 / yfinance 备"

```python
async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    """1d 历史。
    路径: akshare 主源 → yfinance 备份(有 backup_cb)。
    """
    # 主源: akshare
    try:
        return await self._fetch_history_akshare(symbol, start, end)
    except Exception as e:  # noqa: BLE001
        log.warning("us.akshare_history_failed", symbol=symbol, error=str(e))

    # 备份: yfinance(有 circuit breaker)
    if not self.backup_cb.can_execute():
        log.warning("us.yfinance_circuit_open_skip_history", symbol=symbol)
        raise AdapterError(
            f"akshare failed and yfinance circuit open for {symbol}",
            source="us",
        )
    try:
        bars = await self._fetch_history_yfinance(symbol, start, end)
        self.backup_cb.record_success()
        return bars
    except Exception as e:
        self.backup_cb.record_failure()
        raise AdapterError(f"both akshare and yfinance failed for {symbol}: {e}", source="us") from e
```

(`_fetch_history_yfinance` 是当前 `fetch_history` 整段重命名)

### 4.4 `_fetch_history_akshare`

```python
async def _fetch_history_akshare(
    self, symbol: str, start: datetime, end: datetime,
) -> list[Bar]:
    """akshare stock_us_hist 拿 1d。
    ts normalize: 'YYYY-MM-DD' → ET 自然交易日 00:00 → UTC(雷区 3 对称)。
    """
    if self.dir_repo is None:
        raise RuntimeError("dir_repo not injected, akshare path unavailable")

    ak_code = await self._resolve_akshare_code(symbol)
    if ak_code is None:
        raise RuntimeError(f"failed to resolve akshare code for {symbol}")

    sd = start.strftime("%Y%m%d")
    ed = end.strftime("%Y%m%d")
    df = await ak_call(
        "stock_us_hist",
        symbol=ak_code, period="daily", start_date=sd, end_date=ed, adjust="",
        caller=f"us.fetch_history:{symbol}",
    )
    out: list[Bar] = []
    for _, row in df.iterrows():
        date_str = str(row["日期"])
        # ET 自然日 00:00 → UTC
        et_midnight = pd.Timestamp(date_str).tz_localize("America/New_York")
        ts_utc = et_midnight.tz_convert("UTC").to_pydatetime()
        out.append(Bar(
            market="us", symbol=symbol, ts=ts_utc,
            open=Decimal(str(float(row["开盘"]))),
            high=Decimal(str(float(row["最高"]))),
            low=Decimal(str(float(row["最低"]))),
            close=Decimal(str(float(row["收盘"]))),
            volume=int(row["成交量"]) if not pd.isna(row["成交量"]) else 0,
            interval="1d",
        ))
    return out
```

### 4.5 `_resolve_akshare_code`

```python
_AKSHARE_PREFIXES = ["105", "106", "107"]  # NASDAQ, NYSE, AMEX

async def _resolve_akshare_code(self, symbol: str) -> str | None:
    """返回 'X.AAPL' 格式。
    - 已缓存 → 直接返回
    - 未缓存 → 试 105/106/107,首次成功后回写 directory
    - 全失败 → None
    """
    if self.dir_repo is None:
        return None
    cached = await self.dir_repo.get_akshare_code(symbol)
    if cached:
        return cached

    yf_symbol = _to_yfinance_ticker(symbol)  # 复用现有, BRK.B → BRK-B
    # akshare 也用横杠? 实测: 只接受 ticker 直串, 不带交易所后缀
    # BRK.B 在 akshare 应当是 'BRK_B' 或类似, 验证 case
    # 决策: 优先用纯 yf_symbol(横杠), 失败再尝试原 symbol
    for candidate in (yf_symbol, symbol):
        for prefix in _AKSHARE_PREFIXES:
            ak_code = f"{prefix}.{candidate}"
            try:
                df = await ak_call(
                    "stock_us_hist",
                    symbol=ak_code, period="daily",
                    start_date="20260101", end_date="20260110",
                    adjust="",
                    caller=f"us.resolve:{symbol}:{ak_code}",
                )
                if df is not None and len(df) > 0:
                    await self.dir_repo.set_akshare_code(symbol, ak_code)
                    log.info("us.akshare_code_resolved", symbol=symbol, code=ak_code)
                    return ak_code
            except Exception:  # noqa: BLE001
                continue
    log.warning("us.akshare_code_unresolved", symbol=symbol)
    return None
```

**注意**:`BRK.B` 等 class share 在 akshare 对应什么前缀 + 格式不确定,本期不专门处理 — 如果首批用户访问 `BRK.B` 解析失败,后续 ticker case-by-case 加(YAGNI)。

### 4.6 deps.py 注入

```python
@lru_cache(maxsize=1)
def get_us_adapter() -> USAdapter:
    return USAdapter(dir_repo=get_symbol_directory_repo())
```

但当前 USAdapter 通过 registry 创建(`core/adapters/registry.py::AdapterRegistry.from_config`),改:registry 接收 `us_dir_repo` 注入路径。

最简方案:**registry 不变**(无 dir_repo),USAdapter 默认 `dir_repo=None` → fetch_history 时如果没有 dir_repo,跳过 akshare 路径直接用 yfinance(向后兼容)。然后改 `apps/api/deps.py::get_kline_service` 中重新构造 USAdapter:

```python
@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    # 给 us adapter 注入 dir_repo (akshare_code 缓存用)
    if "us" in adapters:
        adapters["us"].dir_repo = get_symbol_directory_repo()
    return KLineService(get_bar_repo(), adapters)
```

(直接给现有 USAdapter 实例 setattr 注入,简单 + 不用动 registry)

### 4.7 前端 UI

`apps/web/lib/intervals.ts::klineTabsForMarket` 改:

```typescript
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  // 美股本期仅支持 1d/1wk/1mo(intraday 暂未接入), 4h 也跳过
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

`detailSignalTabs` 同步 — 美股仅 `1d`(15m/30m/60m/4h 都依赖 intraday):

```typescript
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
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

`apps/web/components/KLineChart.tsx` — 已有"加载中"/"加载失败"逻辑,无需新文案。tab 不显示就够了。

`watchlist-events` 后端美股 4h 已经过滤(spec 老版),本期不变。

### 4.8 Scheduler 影响

US 信号 cron 在 Task 9 加的 `cd:us:15m/30m/60m/4h/1d`。本期 intraday 不接入 → 这些 cron 跑了也没数据。**保留 cron**(无副作用,SignalRepo upsert 幂等),但可以在 spec §10 留扩展点说明 yfinance 解封后 1d 走 akshare、intraday 仍 yfinance 时这些 cron 自动恢复。

---

## 5. 故障矩阵

| 故障 | 现象 | 处理 |
|---|---|---|
| akshare 网络断连(代理 / 网络抖动) | `_fetch_history_akshare` 抛 → fallback yfinance | yfinance 熔断时返 AdapterError → API 500 → 前端"加载失败" |
| akshare 反爬限流 | 同上 | 同上 |
| yfinance 完全 ban | backup_cb 熔断 30 分钟 | snapshot 静默返空,history 抛错 |
| `BRK.B` 类 ticker 在 akshare 解析失败 | `_resolve_akshare_code` 返 None → 抛 → fallback yfinance | 同 yfinance 路径,Alpaca quote 仍 OK |
| 用户首次访问冷门 ticker(directory 没有) | search 懒加载 → 加 directory → fetch_history 时再首次 resolve akshare_code | 慢一次(~3 次试探请求),后续命中 |
| schema 升级失败 | ALTER TABLE 抛(罕见) | 启动期日志 + 影响 us K 线;A 股不影响 |

## 6. 测试策略

### 6.1 单元测试

| 文件 | 范围 |
|---|---|
| `tests/unit/persistence/test_symbol_directory_repo.py` | `_ensure_schema` 幂等;`get_akshare_code` / `set_akshare_code` |
| `tests/unit/adapters/test_us.py` | `_fetch_history_akshare` mock ak_call,断言 ts ET normalize |
| `tests/unit/adapters/test_us.py` | `_resolve_akshare_code` 命中缓存 / 试探 105 → 命中 / 全失败 |
| `tests/unit/adapters/test_us.py` | `fetch_history` akshare 主成功 / 主失败 fallback yfinance / 备熔断后抛 |
| `tests/unit/adapters/test_us.py` | `fetch_snapshot` backup_cb 熔断时静默返空 |

### 6.2 集成测试

不加(`@pytest.mark.integration` 默认不跑,且本任务依赖外部网络)。

### 6.3 前端 tsc

`cd apps/web && npx tsc --noEmit` 必跑。

### 6.4 手工验收

1. `curl /api/symbols/AAPL/bars?interval=1d&days=30` 返回 ≥1 条
2. DuckDB 查 `bars where symbol='AAPL' and interval='1d'` ts 在 `04:00:00` 或 `05:00:00`(EDT/EST)
3. 浏览器 `/symbol/AAPL` 详情页 1d K 线显示;切到 60m 等 tab 不再出现(不显示在 tab 列表里)
4. 关注页 us tab 添加 AAPL → 涨跌幅仍 0(snapshot 不变)→ 进详情页 1d 工作

## 7. 回滚

- 改 `apps/api/deps.py::get_kline_service` 删除 `adapters["us"].dir_repo = ...` 行,USAdapter 自动跳过 akshare 路径直接 yfinance(行为退回到本 spec 之前)
- schema 加的 `akshare_code` 列保留(SQLite ALTER 没法 DROP COLUMN,但留着无害)

## 8. 关键约束摘要(给实施者)

- akshare 走 `ak_call`(SSoT),不直接 `import akshare`(雷区 1)
- yfinance backup 路径必须有自己的 CircuitBreaker(`backup_cb`)
- 1d ts 必须走 `tz_localize('America/New_York').tz_convert('UTC')`(雷区 3 对称)
- `dir_repo` 可选注入,不传则 akshare 路径不可用(向后兼容旧行为)
- `_resolve_akshare_code` 试探顺序:`105.X` → `106.X` → `107.X`,首次成功即缓存
- 业务层 symbol 永远见 `AAPL` / `BRK.B`,akshare code 是 adapter 内部细节
- 前端美股 K 线 tab 仅显示 `1d`/`1wk`/`1mo`;详情页信号 tab 仅 `1d`(intraday 暂未接入)
- 其他市场(A 股/港股/Crypto)tab 配置不动

## 9. 待办与未实施事项(写入 `docs/TODO.md`)

- 美股 intraday 数据源:研究 stooq.com / pandas-datareader / WSJ scraper 等;或等 yfinance ban 解封后接回;或购入 Alpaca paid
- akshare `BRK.B` 类 class share ticker 格式探索
- yfinance 解封后启用熔断恢复路径(目前 backup_cb 已写好,只是 IP 处于持续封禁状态)
- 美股 directory `_US_SEEDS` 启动期批量预热 akshare_code(如果用户体验慢)
