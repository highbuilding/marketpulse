# 美股市场接入设计

**版本**: 2026-05-18
**作者**: zhonghuai + Claude
**状态**: 待实施

---

## 0. 背景

MarketPulse 当前 A 股 / HK / Crypto 三市场骨架完整,US 只有 snapshot + 1d 日线(yfinance),无 intraday、无 CD 信号扫描、无关注页接入。本设计把 US 市场功能对齐到 A 股 90%,并把关注页改造为 4 市场 tab(A 股 / 港股 / 美股 / 加密货币),港股 + 加密货币本期为骨架(tab 显示,内容空)。

## 1. 目标与非目标

### 1.1 目标

- USAdapter 补齐 `fetch_intraday`(yfinance,prepost=True)和 `fetch_history`
- 详情页支持美股 1m/5m/15m/30m/60m/**4h**/1d/1wk/1mo K 线
- CD 信号在 15m/30m/60m/4h/1d 全周期扫描,按 ET 时区 cron(自动跟随夏/冬令时)
- 关注页 4-tab(A 股 / 港股 / 美股 / 加密货币);切 tab 时搜索 scope 跟随该市场
- Symbol directory 内置 ~200 美股种子 + 搜索框懒加载(yfinance verify)
- 前端美股时间显示按 ET(夏冬令时正确处理),其他市场仍 BJT
- 4h 美股语义: prepost 04:00-08:00 / 08:00-12:00 / 12:00-16:00 / 16:00-20:00 ET,每日 4 根

### 1.2 非目标

- ❌ 美股资金流面板(yfinance institutional holders 数据质量不达 A 股东财水平)
- ❌ Dashboard 美股板块新增指数卡(沿用现状)
- ❌ HK / Crypto 关注页内容接入(本期骨架)
- ❌ 富途 SDK 接入(留作 Plan B)
- ❌ Alpaca 历史 K 线(snapshot 够用)
- ❌ K 图 markers 在新 bar 到达时自动同步(上一次会话已说明,本期不做)
- ❌ 跨市场 backtest / 完整历史(1d 只回填到 2020-01-01)
- ❌ Watchlist 后端 data model 改造(保留单 default list,前端按市场 tab 过滤展示)
- ❌ 1h prepost / regular 分桶(业界主流不做数据层切分,留作 UI 层 toggle 扩展)
- ❌ K 线 4h 按时钟对齐 bucket(用当前 group_resample 数组下标切片,容忍偶发 1 小时偏移)

## 2. 数据源决策

| 维度 | 决策 | 备选 |
|---|---|---|
| 主要数据源 | yfinance | Alpaca(已有,snapshot 保留) |
| 历史 1d | yfinance `start=2020-01-01` | 全历史(`period=max`)被排除 |
| Intraday | yfinance `period=60d, interval=Xm, prepost=True` | 1m 限 7d 是接口硬限 |
| 兜底 | Alpaca snapshot(现有) | 富途 SDK 备(本期不做) |

**排除的源**:
- `akshare stock_us_*`: 只有 1d,无 intraday,且涉及 mini_racer 雷区
- `arkfunds.io/api`: 是 ARK 基金持仓 API,不给行情
- 富途 SDK: 需 OpenD daemon + 港股账户 + 紧 quota,本期成本不划算

## 3. 架构改动面

| 层 | 文件 | 性质 |
|---|---|---|
| domain | `core/domain/markets.py` (新) | SSoT `infer_market(symbol)` + `Market` 类型 + `is_crypto(symbol)` |
| adapter | `core/adapters/us.py` | 加 `fetch_intraday`;改写 `fetch_history`;加 `_to_yfinance_ticker`(`BRK.B` ↔ `BRK-B`);加 `verify_ticker` |
| service | `core/services/kline_service.py` | 持有 `dict[market→adapter]`,按 symbol 路由;`_get_daily`/`_get_intraday` 动态 market 查缓存 |
| service | `core/services/symbol_directory_service.py` | 加 `bootstrap_us_seeds()` |
| service | `core/services/signal_service.py` | `scan_many` 加 `market_filter` 参数,按市场过滤 universe |
| scheduler | `core/scheduler/scheduler.py` | 新增 `attach_us_signal_jobs`(`timezone='America/New_York'`);现有 A 股 cron 加 `market_filter='ashare'` |
| intervals | `core/domain/intervals.py` | 4h `crypto_only=True` → False(改由前端按市场过滤 tab) |
| route | `apps/api/routes/symbols.py` | search 加 `?market=` 过滤 + yfinance 懒加载 fallback;`_infer_market` 删除,改 import |
| route | `apps/api/routes/cd_signals.py` | watchlist-events 加 `?market=` 参数 |
| route | `apps/api/routes/watchlists.py` | 首扫 BackgroundTask 覆盖 us symbol(沿用现路径,无改动) |
| web | `apps/web/lib/markets.ts` (新) | `inferMarket` + `marketTz` + `tradingDateKey` + `todayKey` |
| web | `apps/web/lib/signal_time.ts` | 保留 `bjtDateKey` / `todayBjtKey` 作 ashare 别名;增加 market-aware 版本 |
| web | `apps/web/lib/intervals.ts` | `klineTabsForMarket` / `detailSignalTabs(market)` 按市场过滤 4h |
| web | `apps/web/lib/chart_time.ts` | `fmtChartCrosshair`/`fmtChartTick` 接收 `market` + `Intl.DateTimeFormat(timeZone)` |
| web | `apps/web/app/watchlist/page.tsx` | 4-tab + symbolsForTab 前端过滤 + 搜索 scope 跟随 tab |
| web | `apps/web/components/SymbolSearch.tsx` | 接收 `market` prop,传 `?market=` 给后端 |
| web | `apps/web/components/CDSignalPanel.tsx` | 用 `tradingDateKey(market)` 做"当天/历史"分组 |
| web | `apps/web/components/WatchlistSignalsPanel.tsx` | 接收 `market` prop,信号 scope + 分组都按 market |
| web | `apps/web/components/SignalsTable.tsx` | 接收 `market` prop,formatter 按市场选时区 |
| web | `apps/web/components/{KLineChart,IntradayChart}.tsx` | 接收 `market`,`toBarTime` 按市场算时区 offset |

### 3.1 边界守则

- adapter 层负责数据源;新 source 改 adapter 不动 service
- service 层不感知数据源,只按 market 转发到对应 adapter
- 前端组件接收 `market` prop 显式传入,**不在组件内做隐式推断**
- `core/domain/markets.py::infer_market` 是后端 SSoT;`apps/web/lib/markets.ts::inferMarket` 是前端镜像,改一处同步另一处

## 4. 详细设计

### 4.1 `core/domain/markets.py` (SSoT)

```python
from typing import Literal

Market = Literal["ashare", "hk", "us", "crypto"]

def infer_market(symbol: str) -> Market:
    """根据 symbol 字符串推断市场。
    - 600519.SH / 510300.SH / 000001.SZ / 920001.BJ → ashare
    - 9988.HK / HSI.HK                               → hk
    - 含 '/' (如 BTC/USDT)                            → crypto
    - 其他 (AAPL / BRK.B / SPY / ^GSPC)              → us(兜底)
    """
    if symbol.endswith((".SH", ".SZ", ".BJ")):
        return "ashare"
    if symbol.endswith(".HK"):
        return "hk"
    if "/" in symbol:
        return "crypto"
    return "us"

def is_crypto(symbol: str) -> bool:
    return infer_market(symbol) == "crypto"
```

收口点:
- `apps/api/routes/symbols.py::_infer_market` 删除,改 `from core.domain.markets import infer_market`
- `apps/api/routes/cd_signals.py::_is_crypto` 删除,改 `from core.domain.markets import is_crypto`
- `core/scheduler/jobs.py` 重复的 market 推断段删除

### 4.2 USAdapter

#### `fetch_intraday`(新)

```python
async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
    """freq: '1'/'5'/'15'/'30'/'60' min。
    yfinance 限制: 1m=7d, 5m/15m/30m/60m=60d。prepost=True 拿盘前盘后。
    """
    interval_map = {"1": "1m", "5": "5m", "15": "15m",
                    "30": "30m", "60": "60m"}
    yf_interval = interval_map[freq]
    period = "7d" if freq == "1" else "60d"
    yf_symbol = _to_yfinance_ticker(symbol)
    df = await asyncio.to_thread(
        yf.download, yf_symbol,
        period=period, interval=yf_interval,
        prepost=True, progress=False, auto_adjust=False,
    )
    out: list[Bar] = []
    for idx, row in df.iterrows():
        # yfinance intraday 返回的 index 带 ET 时区, 直接 tz_convert UTC
        if idx.tzinfo is None:
            ts_utc = idx.tz_localize("America/New_York").tz_convert("UTC").to_pydatetime()
        else:
            ts_utc = idx.tz_convert("UTC").to_pydatetime()
        # 跳过 NaN 行(yfinance 在 prepost 时段偶发)
        if pd.isna(row["Open"]) or pd.isna(row["Close"]):
            continue
        vol = int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
        out.append(Bar(
            market="us", symbol=symbol, ts=ts_utc,
            open=Decimal(str(float(row["Open"]))),
            high=Decimal(str(float(row["High"]))),
            low=Decimal(str(float(row["Low"]))),
            close=Decimal(str(float(row["Close"]))),
            volume=vol, interval=f"{freq}m",
        ))
    return out
```

#### `fetch_history`(改写,normalize 1d ts 到 ET 自然交易日 00:00)

```python
async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
    yf_symbol = _to_yfinance_ticker(symbol)
    df = await asyncio.to_thread(
        yf.download, yf_symbol,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        progress=False, auto_adjust=False,
    )
    out: list[Bar] = []
    for idx, row in df.iterrows():
        # 与 A 股雷区 3 对称: ts = 该市场本地交易日 00:00 → UTC
        if idx.tzinfo is None:
            local_midnight = idx.tz_localize("America/New_York")
        else:
            local_midnight = idx.tz_convert("America/New_York")
        ts_utc = local_midnight.normalize().tz_convert("UTC").to_pydatetime()
        if pd.isna(row["Open"]) or pd.isna(row["Close"]):
            continue
        out.append(Bar(
            market="us", symbol=symbol, ts=ts_utc,
            open=Decimal(str(float(row["Open"]))),
            high=Decimal(str(float(row["High"]))),
            low=Decimal(str(float(row["Low"]))),
            close=Decimal(str(float(row["Close"]))),
            volume=int(row["Volume"]),
            interval="1d",
        ))
    return out
```

#### `_to_yfinance_ticker`(新模块级函数)

```python
def _to_yfinance_ticker(symbol: str) -> str:
    """Class share 字符转换: BRK.B → BRK-B(yfinance / Alpaca 格式)。
    业务层永远见 BRK.B,adapter 进出口转换。
    """
    return symbol.replace(".", "-")
```

#### `verify_ticker`(新,懒加载用)

```python
async def verify_ticker(self, symbol: str) -> tuple[bool, str | None]:
    """轻量校验 + 拿 long name。用于 directory 懒加载。
    返回 (是否有效, 公司名 | None)。
    """
    yf_symbol = _to_yfinance_ticker(symbol)
    try:
        info = await asyncio.to_thread(
            lambda: yf.Ticker(yf_symbol).fast_info,
        )
        if not getattr(info, "last_price", None):
            return False, None
        # fast_info 不含 name, 再单调一次轻量 info
        long_name = await asyncio.to_thread(
            lambda: getattr(yf.Ticker(yf_symbol), "info", {}).get("longName"),
        )
        return True, long_name
    except Exception:  # noqa: BLE001
        return False, None
```

#### `health`(微调)

`alpaca circuit open` 时不返回 `down`,因为 yfinance 兜底可用 → 改 `degraded`。`disabled` 仅保留作"两个源都不可用"信号(暂时仍是 `missing ALPACA_API_KEY`,因 yfinance 总是可用,不会实际触发)。

### 4.3 KLineService 多市场化

```python
class KLineService:
    def __init__(
        self, bar_repo: BarRepo,
        adapters: dict[str, MarketAdapter],
    ) -> None:
        self.repo = bar_repo
        self.adapters = adapters

    def _adapter_for(self, symbol: str) -> MarketAdapter:
        m = infer_market(symbol)
        a = self.adapters.get(m)
        if a is None:
            raise ValueError(f"no adapter for market={m} (symbol={symbol})")
        return a

    async def _get_daily(self, symbol, start, end):
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval="1d")
        if cached and self._covers(cached, start, end):
            return cached
        bars = await self._adapter_for(symbol).fetch_history(symbol, start, end)
        self.repo.insert_bars(bars)
        return bars

    async def _get_intraday(self, symbol, interval, start, end):
        if interval == "1m":
            bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq="1")
            return [b for b in bars if start <= b.ts <= end]
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval=interval)
        if cached and self._covers(cached, start, end):
            return cached
        freq = interval.replace("m", "")
        bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq=freq)
        self.repo.insert_bars(bars)
        return [b for b in bars if start <= b.ts <= end]
```

4h 分支:

```python
_FOUR_HOUR_GROUP_BY_MARKET: dict[str, int] = {
    "us": 4, "crypto": 4, "ashare": 4, "hk": 4,
}

# get_bars 4h 分支
if interval == "4h":
    market = infer_market(symbol)
    group_size = _FOUR_HOUR_GROUP_BY_MARKET.get(market, 4)
    sixty = await self._get_intraday(symbol, "60m", start, end)
    return _group_resample(sixty, group_size, "4h")
```

**预留扩展点**(本期不实现):`SignalScanService.scan_symbol` 未来可选加 `regular_only: bool = False`,True 时在喂入 CD 指标前按市场 session 过滤掉盘前盘后 bar。这是业界主流"减噪声"路径,UI 配套加 "Extended Hours" toggle。本期不做,记入 `docs/TODO.md`。

### 4.4 Scheduler

新增 US 信号 cron(`apscheduler.triggers.cron.CronTrigger(timezone='America/New_York')`,自动跟夏/冬令时):

```python
def attach_us_signal_jobs(sched, *, signal_scan, watchlist):
    common = dict(args=(signal_scan, watchlist), max_instances=1,
                  coalesce=True, misfire_grace_time=300)
    et = "America/New_York"
    # 15m: ET 04:00-19:45 每 15 分钟
    sched.add_job(scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/15", timezone=et),
        id="cd:us:15m",
        kwargs={"interval": "15m", "market_filter": "us"}, **common)
    # 30m: 同区间每 30 分钟
    sched.add_job(scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/30", timezone=et),
        id="cd:us:30m",
        kwargs={"interval": "30m", "market_filter": "us"}, **common)
    # 60m: 一根收盘 +5,ET 05:05 - 20:05(每小时整点 +5)
    sched.add_job(scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="5-20", minute="5", timezone=et),
        id="cd:us:60m",
        kwargs={"interval": "60m", "market_filter": "us"}, **common)
    # 4h: ET 08:05 / 12:05 / 16:05 / 20:05
    sched.add_job(scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="8,12,16,20", minute="5", timezone=et),
        id="cd:us:4h",
        kwargs={"interval": "4h", "market_filter": "us"}, **common)
    # 1d: ET 20:05
    sched.add_job(scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="20", minute="5", timezone=et),
        id="cd:us:1d",
        kwargs={"interval": "1d", "market_filter": "us"}, **common)
```

**SignalScanService 改造**:

```python
async def scan_many(
    self, symbols: list[str], interval: str,
    *, market_filter: str | None = None,
) -> int:
    if market_filter:
        symbols = [s for s in symbols if infer_market(s) == market_filter]
    total = 0
    for sym in symbols:
        try:
            total += await self.scan_symbol(sym, interval)
        except Exception as e:  # noqa: BLE001
            log.warning("signal.scan_failed", symbol=sym,
                        interval=interval, error=str(e))
    return total
```

**`scan_cd_job` 改造**(接收 market_filter):

```python
async def scan_cd_job(
    signal_scan, watchlist, *, interval, market_filter=None,
):
    universe = await watchlist.dynamic_universe()
    await signal_scan.scan_many(universe, interval, market_filter=market_filter)
```

**配套**:现有 A 股 cron 全部加 `kwargs={"interval": "15m", "market_filter": "ashare"}`,避免重叠扫描。

### 4.5 Symbol Directory

#### 美股 seeds(`_US_SEEDS`)

约 200 条,覆盖:
- 大盘指数 ETF: SPY / QQQ / DIA / IWM / VTI / VOO
- 行业 ETF: XLF / XLK / XLE / XLV / XLI / XLY / XLP / XLU / XLB / XLRE / XLC
- 主题 ETF: ARKK / SMH / SOXX / GLD / SLV / TLT
- 道指 30 + NASDAQ100 头 ~50 + S&P100 补足 + 中概股 BABA / PDD / NIO / JD / BIDU

文件放 `core/services/symbol_directory_service.py` 内或独立 `_us_seeds.py`(实施时决定)。

#### Bootstrap 顺序

`apps/api/main.py::lifespan`:

```python
await dir_svc.bootstrap_seeds()        # 已有: 指数种子
if await dir_svc.count() < 100:
    await dir_svc.refresh_ashare()     # 已有: A 股 7000+
await dir_svc.bootstrap_us_seeds()     # 新增: 静态写库,无外部调用
```

#### 懒加载

`apps/api/routes/symbols.py::search` 在 directory 查询 0 hit 后:

```python
@router.get("/search", response_model=SearchResponse)
async def search(
    q: str,
    market: str | None = Query(None),
    limit: int = 20,
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
    registry: AdapterRegistry = Depends(get_registry),
) -> SearchResponse:
    hits = await svc.search(q, limit, market=market)
    if hits:
        return SearchResponse(hits=[...])

    # 懒加载: 仅当 q 像美股 ticker 且 market 为 us 或未指定时
    if market in (None, "us") and _looks_like_us_ticker(q):
        us_adapter = registry.get("us")
        ok, name = await us_adapter.verify_ticker(q.upper())
        if ok:
            await svc.upsert_one(q.upper(), name or q.upper(), "us")
            return SearchResponse(hits=[SearchHit(
                symbol=q.upper(), name=name or q.upper(), market="us",
            )])
    return SearchResponse(hits=[])

def _looks_like_us_ticker(q: str) -> bool:
    # 1-5 个字母,可选 `.` + 1 字母后缀(如 BRK.B)
    return bool(re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", q.upper()))
```

`SymbolDirectoryService.search` 增加 `market` 参数;`SymbolDirectoryRepo.search` 增加 `WHERE market = ?` 过滤。

### 4.6 前端

#### `apps/web/lib/markets.ts`(新,SSoT 镜像)

```typescript
export type Market = 'ashare' | 'hk' | 'us' | 'crypto'

export function inferMarket(symbol: string): Market {
  if (/\.(SH|SZ|BJ)$/.test(symbol)) return 'ashare'
  if (symbol.endsWith('.HK')) return 'hk'
  if (symbol.includes('/')) return 'crypto'
  return 'us'
}

const TZ: Record<Market, string> = {
  ashare: 'Asia/Shanghai',
  hk:     'Asia/Hong_Kong',
  us:     'America/New_York',
  crypto: 'Asia/Shanghai',
}

export function marketTz(market: Market): string { return TZ[market] }

export function tradingDateKey(iso: string, market: Market): string {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: TZ[market] })
}

export function todayKey(market: Market): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: TZ[market] })
}

// 给定 ISO 时刻,返回该市场时区相对 UTC 的 offset(秒)。用于 chart 渲染。
export function tzOffsetSeconds(market: Market, iso: string): number {
  const date = new Date(iso)
  const local = new Date(date.toLocaleString('en-US', { timeZone: TZ[market] }))
  const utc = new Date(date.toLocaleString('en-US', { timeZone: 'UTC' }))
  return (local.getTime() - utc.getTime()) / 1000
}
```

#### `apps/web/app/watchlist/page.tsx` 4-tab

```tsx
const MARKET_TABS = [
  { key: 'ashare', label: 'A 股' },
  { key: 'hk',     label: '港股' },
  { key: 'us',     label: '美股' },
  { key: 'crypto', label: '加密货币' },
] as const

const [marketTab, setMarketTab] = useState<Market>('ashare')

const symbolsForTab = useMemo(
  () => (items?.symbols ?? []).filter((s) => inferMarket(s) === marketTab),
  [items, marketTab],
)

// HK / Crypto 显示空骨架文案
const showSkeleton = (marketTab === 'hk' || marketTab === 'crypto')
                     && symbolsForTab.length === 0

// SymbolSearch 强制按 tab 重 mount(切 tab 清空 query)
<SymbolSearch
  key={marketTab}
  market={marketTab}
  placeholder={...}
  onSelect={...}
/>
```

#### `SymbolSearch`

接收 `market: Market` prop,传给 `searchSymbols(q, limit, market)`,后端按 `?market=` 过滤。

#### `WatchlistSignalsPanel`

接收 `market: Market` prop,内部:
- `fetchWatchlistEvents(interval, limit, market)` 加 market 参数 → 后端按 market 过滤
- `tradingDateKey(s.bar_ts, market)` / `todayKey(market)` 切分当天 vs 历史

#### `CDSignalPanel`

接收 `market: Market` prop(从 page.tsx 通过 `profile?.market` 拿),"当天 vs 历史"分组用 `tradingDateKey(market)`。

#### `SignalsTable::fmtSignalTs`

接收 `market` prop,formatter 内部用 `Intl.DateTimeFormat('en-CA', { timeZone: marketTz(market) })`.

#### `KLineChart` / `IntradayChart`

接收 `market: Market` prop。`toBarTime` 改:

```typescript
function toBarTime(iso: string, interval: Interval, market: Market): Time {
  if (INTRADAY.has(interval)) {
    return ((new Date(iso).getTime() / 1000) + tzOffsetSeconds(market, iso)) as Time
  }
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: marketTz(market) }) as Time
}
```

`fmtChartCrosshair` / `fmtChartTick` 用 `Intl.DateTimeFormat` 替代 `getUTC*`,内部按 `marketTz(market)`.

#### `intervals.ts`

```typescript
export function klineTabsForMarket(market: Market | null): IntervalTab[] {
  const all = ALL_KLINE_TABS
  if (market === 'us' || market === 'crypto') return all
  return all.filter((t) => t.key !== '4h')
}

export function detailSignalTabs(market: Market): SignalIntervalTab[] {
  const all = ALL_SIGNAL_TABS
  if (market === 'us' || market === 'crypto') return all
  return all.filter((t) => t.key !== '4h')
}
```

`detailSignalTabs` 当前是无参函数,签名加 market 参数。

## 5. 数据流

```
US 详情页加载
  → fetchSymbolProfile(AAPL)
    → /api/symbols/AAPL/profile
      → infer_market("AAPL") → "us"
      → directory.get_name("AAPL")
  → 前端拿到 profile.market="us"
  → klineTabsForMarket("us") → 含 4h
  → 切 60m tab
  → fetchBars(AAPL, "60m", 60)
    → /api/symbols/AAPL/bars?interval=60m&days=60
      → kline_service.get_bars("AAPL", interval="60m", ...)
        → infer_market("AAPL") → "us"
        → 查 DuckDB(market="us") → miss
        → us_adapter.fetch_intraday("AAPL", freq="60")
          → yf.download("AAPL", period="60d", interval="60m", prepost=True)
        → bar_repo.insert_bars()
        → 返回 bars
  → KLineChart 渲染(market="us" → ET 时区)
```

```
ET 14:35 → scheduler 触发 cd:us:60m
  → scan_cd_job(interval="60m", market_filter="us")
    → universe = watchlist.dynamic_universe()
    → 过滤: [s for s in universe if infer_market(s)=="us"]
    → 对每只 symbol:
      → kline_service.get_bars(symbol, "60m", lookback=400根)
      → cd_indicator.calc(close_series) → 新信号
      → signal_repo.upsert_many(new_signals)
```

## 6. 故障矩阵

| 故障 | 现象 | 处理 |
|---|---|---|
| yfinance 国内被限速 | K 线加载慢/失败 | adapter CircuitBreaker;UI "加载失败"提示 |
| 单只 us symbol 在 yfinance 失效(退市) | adapter 返回 [] | service warn 日志;UI 显示"无数据";不阻断其他 symbol |
| ET 夏令时切换那周(03/08, 11/01)offset 错 | UI 时间偏 1 小时 | `tzOffsetSeconds` 用 `Intl.DateTimeFormat` 而非常量;unit test 覆盖切换 |
| `BRK.B` 类点号 ticker 错路由 | 找不到 / 抓不到 | `infer_market` 用 `.SH/.SZ/.BJ/.HK` 白名单优先 |
| watchlist 切 tab 时 SymbolSearch 未重置 | 上一个 tab 的搜索词残留 | `key={marketTab}` force remount |
| US scheduler 把 A 股 cron 也跑了 | 重复扫描 / 互相覆盖 | scan_cd_job 必传 `market_filter`;A 股 cron 也加 |
| 4h bucket 偶发偏移 | 某天 4h 信号位置不准 | YAGNI 不修;记 `docs/TODO.md` |
| 美股 symbol 添加后首扫慢 | 6 秒未见信号 | 沿用现有 BackgroundTask 路径,与 A 股一致 |

## 7. 测试

### 7.1 单元测试

| 文件 | 范围 |
|---|---|
| `tests/unit/domain/test_markets.py` | `infer_market` 各种 symbol 格式(`600519.SH` / `9988.HK` / `BTC/USDT` / `AAPL` / `BRK.B` / `^GSPC`) |
| `tests/unit/adapters/test_us.py` | `fetch_intraday` mock yfinance,验证 NaN 丢弃 / ts UTC 转换 / `BRK.B` → `BRK-B` |
| `tests/unit/adapters/test_us.py` | `fetch_history` 1d ts normalize 到 ET 自然日 00:00 |
| `tests/unit/adapters/test_us.py` | `_to_yfinance_ticker` 转换 |
| `tests/unit/services/test_kline_service_routing.py` | `_adapter_for(symbol)` 路由正确(us 用 USAdapter,A 股用 AShareAdapter) |
| `tests/unit/services/test_signal_market_filter.py` | `scan_many(market_filter='us')` 过滤逻辑 |
| `tests/unit/web/test_markets_ts.test.ts` | `inferMarket` / `tradingDateKey` / `tzOffsetSeconds`(覆盖 2026-03-08 EDT 切换前后) |

### 7.2 集成测试(`@pytest.mark.integration`,默认不跑)

- 真实 `yfinance.download("AAPL", ...)` 拉 1d / 60m,断言 schema 兼容
- 完整 cd_scan(AAPL × 5 interval),检查 SignalRepo 写入

### 7.3 前端验证

- `cd apps/web && npx tsc --noEmit` 必跑
- 浏览器手工:
  - watchlist 切 4 tab,每个 tab 搜索 scope 正确,HK + Crypto 显示骨架文案
  - 添加 AAPL(seed)→ us tab 列表显示,详情页 1d/60m/4h 各跳一遍,时间显示 ET
  - 添加 PLTR(非 seed)→ 搜索框 verify + 入库 + 添加成功
  - 详情页 1d "当天 vs 历史"分组:ET 自然日切分(ET 2026-05-18 信号在 ET 5/18 显示当天,ET 5/19 后落入历史)
  - 详情页 4h tab 显示 4 根/天,bucket 边界 ET 04/08/12/16/20

## 8. 回滚

- **代码回滚**:`apps/api/main.py` 注释 `bootstrap_us_seeds` + scheduler 不挂 us 分支,前端 `MARKET_TABS` 数组只留 ashare。无需 revert 整 PR。
- **Schema 回滚**:不需要,无 schema 改动。

## 9. 待办与未实施事项(写入 `docs/TODO.md`)

- `signal_service.scan_symbol(regular_only=)` 美股 prepost 噪声过滤选项 + UI "Extended Hours" toggle
- 4h bucket 按时钟对齐(消除 yfinance 偶发缺 bar 时的偏移)
- 富途 SDK 接入(yfinance 失效时的 Plan B)
- 美股 dashboard 板块卡(SPY / QQQ / DIA 主要指数代理)
- 美股资金流(institutional holders / 13F)— 待数据源调研
- HK / Crypto 关注页内容接入(本期骨架,功能开发中)
- K 图 markers 新 bar 自动同步(SWR refreshInterval for chart bars in trading hours)

## 10. 关键约束摘要(给实施者的"红线")

- 业务代码永远见 `BRK.B`,不见 `BRK-B`;转换只在 `USAdapter` 进出口
- `infer_market` 用白名单匹配后缀(`.SH/.SZ/.BJ/.HK`),不能"含点号 → 非 us"黑名单
- 1d bar ts 必须按市场本地交易日 00:00 normalize(A 股 BJT,US ET)
- scheduler cron 每个 job 都必须传 `market_filter`,避免跨市场污染
- `tzOffsetSeconds` 必须基于具体 iso 时刻算,不能用常量(夏冬令时)
- 切 watchlist tab 时,SymbolSearch 必须 force remount,清空 query
- yfinance 调用都在 `asyncio.to_thread` 里,不阻塞事件循环
