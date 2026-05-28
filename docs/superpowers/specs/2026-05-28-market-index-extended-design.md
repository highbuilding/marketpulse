# 全市场大盘指数 + 扩展字段 设计文档

> **日期**: 2026-05-28
> **状态**: Spec — 待 Plan 拆分
> **依赖**: Plan 1+2+3 + 2026-05-28 IndexCard prev_close 修复

## 0. 背景与目标

`IndexCard` 当前只显示 **指数名 + 当前价 + 涨跌幅 + 5min 小图**。
对一个看盘工具,这远远不够 — 用户决策时第一时间需要的还有:

- **大盘资金净流入** — A 股/港股的"风向标"指标
- **今日成交额** — 大盘活跃度
- **同比成交额(vs 上一日同时段)** — 量能变化

而且当前 4 个市场覆盖不一致:

| 市场 | 5m 序列 | prev_close | 资金 | 成交额 | 同比 |
|---|---|---|---|---|---|
| A 股 (8 指数) | ✅ | ✅(2026-05-28 补) | ❌ | ❌ | ❌ |
| 港股 (3 指数) | ❌ stale 兜底 | ❌ | ❌ | ❌ | ❌ |
| 美股 (DJI/NDX/SPX) | ❌ 无 | ❌ | — 不适用 | ❌ | ❌ |
| Crypto (BTC/ETH) | ❌ 无 | ❌ | — 不适用 | ❌ | ❌ |

本设计**统一补齐**,使 IndexCard 在 4 个市场展示一致的多维信息。

---

## 1. 设计原则映射

| Spec 原则 | 本设计应用 |
|---|---|
| 1. 开源免费优先 | sina HTTP / akshare / yfinance / Binance 公开 API,无付费源 |
| 2. 优雅降级 | 任一字段失败返 `null`,UI 整行隐藏,主路径不受影响 |
| 3. 国内可用 | A 股优先 sina,akshare 走代理(`MARKETPULSE_PROXY_URL`) |
| 4. 决策支持 | 不做交易、仅展示,与项目定位一致 |
| 5. 单一可跑 | 复用现有 `cache:index:` namespace,SQLite 加 1 张表,不引入新中间件 |

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│  collector (8788) — 唯一写者                             │
│                                                          │
│  ┌─ index_minute (A 股, 30s 已有, 扩展字段)──┐           │
│  │  ① 拉 5m 序列 (sina, 已有)               │           │
│  │  ② 拉 prev_close 批 (sina spot, 已有)    │           │
│  │  ③ 拉 fund_inflow (akshare 北向)         │ ← 新       │
│  │  ④ 拉 amount (akshare sse+szse 摘要)     │ ← 新       │
│  │  ⑤ 算 amount_ratio (查 SQLite 同时段)    │ ← 新       │
│  └──────────────────────────────────────────┘           │
│                                                          │
│  ┌─ hk_index_minute (港股, 30s) ── 新 ──────┐           │
│  │  类似结构,数据源换为:                      │           │
│  │  - akshare stock_hk_index_daily_em (5m)   │           │
│  │  - akshare stock_hsgt_north_net_flow_in   │           │
│  │    (反向 = 南向净流入)                     │           │
│  │  - akshare stock_hk_index_spot_em (摘要)   │           │
│  └──────────────────────────────────────────┘           │
│                                                          │
│  ┌─ us_index_minute (美股, 60s) ── 新 ──────┐           │
│  │  - yfinance ^DJI/^IXIC/^GSPC 5m         │           │
│  │  - 不拉资金 (美股无北向概念)              │           │
│  │  - amount = ETF 代理 (DIA/QQQ/SPY) 5m    │           │
│  │    Volume × Close 累加                    │           │
│  └──────────────────────────────────────────┘           │
│                                                          │
│  ┌─ crypto_index_minute (Crypto, 60s) ── 新 ┐           │
│  │  - Binance klines (5m) BTC/USDT, ETH/USDT │           │
│  │  - amount = quoteVolume_24h 直接给        │           │
│  │  - amount_ratio = vs 前 24h 滚动 (klines  │           │
│  │    interval=1d limit=2)                   │           │
│  │  - 不入 SQLite 基线表                      │           │
│  └──────────────────────────────────────────┘           │
│                                                          │
│  ┌─ market_amount_baseline_persist (1次/日) ─┐           │
│  │  写当日 5min 累计成交额 → SQLite 基线表    │           │
│  │  - A 股 BJT 15:35                          │           │
│  │  - 港股 BJT 16:05                          │           │
│  │  - 美股 BJT 04:05 (= ET 16:05)             │           │
│  │  - Crypto 不需要 (Binance 现成)            │           │
│  └────────────────────────────────────────────┘          │
│                                                          │
│  写入: cache:index:{symbol}:minute:1 (复用,扩 payload)  │
└─────────────────────────────────────────────────────────┘
       ▲ 读
       │
┌──────────────────────────────────────────────────────┐
│  api (8787)                                          │
│  GET /api/indices/{symbol}/minute                    │
│  → IndexMinuteResponse (新 market_extras 字段)        │
└──────────────────────────────────────────────────────┘
       ▲
       │ HTTP
┌──────────────────────────────────────────────────────┐
│  web — IndexCard 复用 (apps/web/components/IndexCard) │
│  - 行 1: 指数名 + 当前价                              │
│  - 行 2: symbol + 涨跌点 + 涨跌幅                      │
│  - 行 3 (有则显示): 资金行 - "北向: +12.3亿"           │
│  - 行 4 (有则显示): 成交额 + 同比                      │
│  - 5m 小图                                            │
└──────────────────────────────────────────────────────┘
```

**进程职责硬约束**(spec §1):
- collector 唯一写;api 只读 cache;前端 SWR 30s 刷新
- ak_call 全部走三层中间件 (Outlet → Ratelimit → Breaker)
- 非交易日由 `is_trading_day(market)` 跳过

---

## 3. Schema

### 3.1 `IndexMinuteResponse` 扩展

```python
class MarketExtras(BaseModel):
    """大盘附加字段。任一字段为 None → 前端整行隐藏。"""
    fund_inflow: float | None = None        # 北向 / 南向 净流入(亿,正=流入)
    fund_inflow_label: str | None = None    # "北向" / "南向" / None
    amount: float | None = None              # 累计成交额(亿,本币)
    amount_unit: str | None = None           # "亿元" / "亿港元" / "亿美元" / "亿USDT"
    amount_ratio: float | None = None        # 今日 / 同时段基线 - 1.0
                                             # 正例 +0.052 = +5.2% 比昨天同时刻多

class IndexMinuteResponse(BaseModel):
    symbol: str
    name: str
    granularity: str                # "5m" 或 "1d"
    prev_close: float | None = None
    points: list[MinutePoint]
    market_extras: MarketExtras = MarketExtras()  # 新
    meta: IndexMeta = IndexMeta()
```

**为什么嵌套不平铺**:前端 `if (data.market_extras.fund_inflow != null)` 比检查多个独立字段更清晰,且方便未来扩字段(GDP、PMI 等)。

### 3.2 Symbol 命名

沿用现有 SSoT (`core/domain/markets.py::infer_market`),**零改动**:

| 市场 | 大盘指数 symbol(对外+yfinance/Binance) |
|---|---|
| A 股 | `000001.SH` ... `399006.SZ` (8 个,已有) |
| 港股 | `HSI.HK` `HSTECH.HK` `HSCEI.HK` (3 个,已注册) |
| 美股 | `^DJI` `^IXIC` `^GSPC` (yfinance 原生,`infer_market` 落 us) |
| Crypto | `BTC/USDT` `ETH/USDT` (现 SSoT 用 `/` 表 crypto) |

URL 安全性:`^GSPC` 在 URL 里编码成 `%5EGSPC`,前端 `encodeURIComponent` 已处理。

### 3.3 SQLite 新表 `market_amount_baseline`

```sql
CREATE TABLE IF NOT EXISTS market_amount_baseline (
    market        TEXT NOT NULL,        -- 'ashare' / 'hk' / 'us'
                                        -- crypto 不入表 (Binance 24h ticker 现成)
    trading_date  TEXT NOT NULL,        -- 'YYYY-MM-DD' (本市场所在地自然日)
    ts_5m_offset  INTEGER NOT NULL,     -- 当日开盘后第 N 个 5min 桶 (0-based)
    cum_amount    REAL NOT NULL,        -- 累计成交额(原始单位:元 / 港元 / USD)
    PRIMARY KEY (market, trading_date, ts_5m_offset)
);
CREATE INDEX IF NOT EXISTS idx_baseline_market_date
    ON market_amount_baseline(market, trading_date DESC);
```

**保留策略**: 每日 cron 删 20 天前数据(给同比留足缓冲)。

**查询**:
```sql
-- A 股 / 港股: 上一交易日同 offset
SELECT cum_amount FROM market_amount_baseline
WHERE market = ? AND ts_5m_offset = ? AND trading_date < ?
ORDER BY trading_date DESC LIMIT 1;

-- 美股: Relative Volume 10D
SELECT AVG(cum_amount) FROM market_amount_baseline
WHERE market = 'us' AND ts_5m_offset = ? AND trading_date < ?
ORDER BY trading_date DESC LIMIT 10;
```

---

## 4. 数据源选型 & 取舍

### 4.1 A 股

| 字段 | 数据源 | akshare 接口 | 频率 |
|---|---|---|---|
| 5m 序列 | sina(已有) | `stock_zh_a_minute` | 30s |
| prev_close | sina spot(已有) | `hq_str_*` | 30s |
| fund_inflow | akshare 北向 | `stock_hsgt_fund_flow_summary` | 30s(本身延迟 1min) |
| amount | akshare 交易所摘要 | `stock_sse_summary` + `stock_szse_summary` | 30s |

**为什么不用 `stock_zh_a_spot_em` 聚合个股**:5000+ 股票一次拉,EM 限频且耗时 8-30s,超 ak_call 默认超时;交易所摘要权威且单次调用。

### 4.2 港股

| 字段 | 数据源 |
|---|---|
| 5m 序列 | akshare `stock_hk_index_daily_em(symbol)` |
| prev_close | akshare `stock_hk_index_spot_em()` 摘要 |
| fund_inflow | akshare `stock_hsgt_north_net_flow_in("北向")` 反向 = 南向 |
| amount | akshare `stock_hk_index_spot_em()` 摘要 amount 字段 |

### 4.3 美股

| 字段 | 数据源 |
|---|---|
| 5m 序列 | yfinance `Ticker("^DJI").history(period="1d", interval="5m")` |
| prev_close | yfinance `Ticker("^DJI").info["previousClose"]` |
| fund_inflow | — 不显示 |
| amount | **ETF 代理**: yfinance `Ticker("DIA"/"QQQ"/"SPY").history(...).Close × Volume 累加` |

**为什么 amount 用 ETF 代理**:美股指数本身没有成交额(指数是计算结果)。Bloomberg/TradingView 在指数页显示的"Volume"实际就是对应 ETF 成交额。映射:

```python
US_INDEX_VOLUME_PROXY = {
    "^DJI":  "DIA",
    "^IXIC": "QQQ",
    "^GSPC": "SPY",
}
```

### 4.4 Crypto

| 字段 | 数据源 |
|---|---|
| 5m 序列 | Binance `/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=288`(24h) |
| prev_close | Binance `/api/v3/klines?interval=1d&limit=2` 取倒数第 2 根 close(更稳定,不受 5m 边界抖动影响) |
| fund_inflow | — 不显示 |
| amount | Binance `/api/v3/ticker/24hr` `quoteVolume`(过去 24h USDT) |
| amount_ratio | `quoteVolume_24h / quoteVolume_prev_24h - 1`,前 24h 用 `klines interval=1d limit=2` |

**Crypto 同比口径**:24h 滚动而非"日历日 vs 昨天",符合行业惯例(CoinMarketCap / TradingView)。

---

## 5. 涨跌幅 / 同比 计算口径

### 5.1 涨跌幅(已修复,沿用)

```
change_pct = (last_close - prev_close) / prev_close * 100
change_amount = last_close - prev_close
```

**绝不**用 `(last - first) / first` —  那把跳空缺口算进涨跌幅了(2026-05-28 修复)。

### 5.2 同比成交额

| 市场 | 口径 | 公式 |
|---|---|---|
| A 股 | 同时段进度比 | `today_cum / prev_day_cum_at_same_offset - 1` |
| 港股 | 同时段进度比(代码复用 A 股) | 同 |
| 美股 | Relative Volume 10D | `today_cum / mean(prev_10_days_cum_at_same_offset) - 1` |
| Crypto | 24h 滚动同比 | `quoteVolume_24h / quoteVolume_prev_24h - 1` |

**同时段进度比的计算时机**:
1. collector job 每次 refresh 时:
2. `now_offset_5m = (now_local - market_open_local).seconds // 300`
3. `today_cum = market_extras.amount`(本次拉到的当日累计)
4. `baseline_cum = SELECT cum_amount FROM market_amount_baseline WHERE ts_5m_offset=now_offset_5m AND ... ORDER BY date DESC LIMIT 1`
5. `amount_ratio = today_cum / baseline_cum - 1`(任一为 0 或 None → ratio = None)

### 5.3 基线持久化时机

每日收盘后 cron(`market_amount_baseline_persist`):
- A 股 BJT 15:35(收盘 15:00 + 30min 缓冲让交易所摘要稳定)
- 港股 BJT 16:05(港股 16:00 收盘)
- 美股 ET 16:05(冬夏令时自动跟随,APScheduler `tz='America/New_York'`)
- Crypto: **不需要**

每次写入会**重新生成**当日全部 5m offset 行,并删除当日已有同 offset 数据(防部分写过的"半成品")。

---

## 6. 错误处理 / 降级矩阵

| 失败场景 | 字段表现 | UI 表现 |
|---|---|---|
| 5m 序列拉不到 | `points: []` + `meta.stale=True` | 整卡片置灰(已有 StaleBadge) |
| prev_close 拉不到 | `prev_close: null` | 涨跌幅 fallback 到首点(2026-05-28 已实现) |
| `fund_inflow` 失败 | `market_extras.fund_inflow=null` | 第 3 行**整行隐藏** |
| `amount` 失败 | `market_extras.amount=null` | 第 4 行**整行隐藏** |
| `amount_ratio` 失败(基线缺) | `amount=...` + `amount_ratio=null` | 第 4 行只显示"成交 8421亿",无同比 |
| 整个 `market_extras` 全失败 | `MarketExtras()` 全 None | 退化成现 IndexCard(行 3、4 全隐) |

**实现要求**:每个外部调用独立 try/except,**绝不**让一个失败拖垮整个 job。

### 6.1 冷启动(系统首次部署)

- 第 1 个交易日:`market_amount_baseline` 表为空 → `amount_ratio = null` 整天 → 第 4 行只显示成交额
- 收盘后 cron 写入第 1 日基线
- 第 2 个交易日开始:`amount_ratio` 正常计算

**spec 写明**:同比字段需要 1 个完整交易日预热。

### 6.2 节假日跳过

每个 `*_index_minute` job 顶层:
```python
from core.domain.market_calendar import is_trading_day
if not is_trading_day(market):
    log.debug(f"{market}_index_minute.skip_non_trading_day")
    return
```

Crypto 例外(7×24h 永远跑)。

---

## 7. 前端 IndexCard 改动

### 7.1 渲染逻辑

```tsx
// apps/web/components/IndexCard.tsx
const me = data?.market_extras
const showFundRow = me?.fund_inflow != null && me?.fund_inflow_label
const showAmountRow = me?.amount != null

return (
  <a ...>
    {/* 行 1: name + price */}
    {/* 行 2: symbol + change_amount + change_pct */}
    <MiniChart .../>
    <div>当日分时</div>
    {showFundRow && (
      <div className="text-xs text-neutral-400 mt-1">
        {me.fund_inflow_label}: {formatFundInflow(me.fund_inflow)}
      </div>
    )}
    {showAmountRow && (
      <div className="text-xs text-neutral-400">
        成交 {formatAmount(me.amount, me.amount_unit)}
        {me.amount_ratio != null && (
          <span className={amount_ratio >= 0 ? "text-red-400" : "text-green-400"}>
            {' '}同比 {formatPct(me.amount_ratio)}
          </span>
        )}
      </div>
    )}
  </a>
)
```

### 7.2 数字格式化

| 输入 | 输出 |
|---|---|
| `fund_inflow=12.345` (亿) | `+12.3亿` |
| `fund_inflow=-3.567` | `-3.6亿` |
| `amount=8421.5`,`unit="亿元"` | `8421亿元` |
| `amount=185.3`,`unit="亿USDT"` | `185亿USDT` |
| `amount_ratio=0.0521` | `+5.2%` |
| `amount_ratio=-0.123` | `-12.3%` |

**A 股涨色与同比同色**:涨跌幅红涨绿跌(中国市场惯例),同比沿用同色系 — `amount_ratio > 0` 红色(成交放大,情绪活跃),`< 0` 绿色。

---

## 8. Cron 调度

| Job | 频率 | 触发市场 | Leader 门控 |
|---|---|---|---|
| `index_minute.refresh_all_indices` (已有) | 每 30s (`IntervalTrigger(seconds=30)`) | A 股交易时段 | ✅ |
| `hk_index_minute.refresh_all_hk` | 每 30s | 港股交易时段 (BJT 9:30-12:00 + 13:00-16:00) | ✅ |
| `us_index_minute.refresh_all_us` | 每 60s | 美股交易时段 (ET 9:30-16:00) | ✅ |
| `crypto_index_minute.refresh_all_crypto` | 每 60s | 7×24h | ✅ |
| `market_amount_baseline_persist.persist_ashare` | `CronTrigger(hour=15, minute=35, timezone='Asia/Shanghai')` | A 股交易日 | ✅ |
| `market_amount_baseline_persist.persist_hk` | `CronTrigger(hour=16, minute=5, timezone='Asia/Shanghai')` | 港股交易日 | ✅ |
| `market_amount_baseline_persist.persist_us` | `CronTrigger(hour=16, minute=5, timezone='America/New_York')`(冬夏令时自动跟随) | 美股交易日 | ✅ |
| `market_amount_baseline_persist.cleanup` | `CronTrigger(hour=3, minute=0, timezone='Asia/Shanghai')`(每日清理 20 天前) | — | ✅ |

所有 cron 经 `_leader_gated()` 包装。

---

## 9. 测试策略

| 层 | 内容 | 文件 |
|---|---|---|
| 单测 | `MarketExtras` 默认全 None 序列化字段齐 | `tests/unit/api/test_indices_route.py` 扩 |
| 单测 | A 股 fund_inflow 失败 + amount 成功的混合场景 → cache payload `fund_inflow=None`,`amount=...` | `tests/unit/collector/jobs/test_index_minute.py` 扩 |
| 单测 | `market_amount_baseline` 写入 + 查询(同 offset / 上一交易日 / 10 日均) | `tests/unit/storage/test_market_amount_baseline.py` 新 |
| 单测 | 港股 / 美股 / Crypto 各 1 个 job: happy-path + 网络失败 fallback | `tests/unit/collector/jobs/test_{hk,us,crypto}_index_minute.py` 新 |
| 单测 | amount_ratio 计算: 基线缺返 None / today=0 返 None / 正常计算 | 同上 |
| 集成 | 真实 A 股 refresh,assert cache 字段齐(@pytest.mark.integration) | `tests/integration/test_market_index_e2e.py` 新 |
| 手工 | 浏览器看 4 个市场 IndexCard 展示是否正确(无 frontend test 框架) | dev server |

---

## 10. 文件清单(增量)

```
core/storage/
  market_amount_baseline_repo.py  ← 新 (SQLite 读写)
core/services/
  market_amount_service.py        ← 新 (统一各市场 amount 拉取 + ratio 计算)
core/integrations/
  binance_client.py               ← 新 (Crypto klines / ticker24hr,薄)
apps/collector/jobs/
  index_minute.py                 ← 改 (加 fund_inflow + amount + ratio)
  hk_index_minute.py              ← 新
  us_index_minute.py              ← 新
  crypto_index_minute.py          ← 新
  market_amount_baseline_persist.py ← 新
core/scheduler/scheduler.py       ← 改 (attach 4 个新 job + persist + cleanup)
apps/api/routes/indices.py        ← 改 (扩 IndexMinuteResponse.market_extras)
                                  ← 改 (HK 走 5m 路径, 不再 stale 兜底)
                                  ← 改 (新增 us / crypto 路径)
apps/web/lib/types.ts             ← 改 (IndexMinuteResponse 加 market_extras)
apps/web/components/IndexCard.tsx ← 改 (渲染行 3 行 4)
apps/web/lib/format.ts            ← 改/新 (formatAmount / formatPct / formatFundInflow)
core/cache/keys.py                ← 不改 (cache_index_minute 已支持任意 symbol)
core/domain/markets.py            ← 不改 (infer_market 已覆盖)

CLAUDE.md                         ← 改 (数据流核心路径表加 4 行)
docs/TODO.md                      ← 改 (划掉 HK 指数 collector 待办)
```

---

## 11. 不做的事(YAGNI)

- 不做 IndexCard hover 展开 — 字段全部直接展示(决策 UI 形态时已确认)
- 不做"量比"行业标准计算(过去 5 日均量) — 同比口径选择 B 同时段进度比
- 不做美股/Crypto 资金流概念 — 没有可信免费数据源
- 不做基线写入实时(逐 5m 写) — 仅日终 1 次,代价极小
- 不做 frontend 测试框架引入 — 浏览器手工验证
- 不开新 cache namespace — 复用 `cache:index:`

---

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| akshare 新增接口被东财限频 | 三层中间件 breaker + ratelimit 已覆盖,失败回 None 不阻塞主路径 |
| yfinance 限频 / 被墙 | 走代理 (`MARKETPULSE_PROXY_URL`),熔断保护已有 |
| Binance 在墙内被限 | 走代理同上 |
| amount 单位混乱(亿 vs 元) | `MarketExtras.amount_unit` 显式带单位标签;格式化函数读 unit |
| 同比基线 SQLite 表写漏(机器关机过夜) | cron 容错: 启动时若发现昨日基线缺,可手动 `python -m apps.collector.cli backfill_baseline --market ashare --date YYYY-MM-DD`(可选,不阻塞主路径) |
| 美股 ETF 代理与指数偏离 | 用户认知与华尔街看盘工具一致;spec 注明这是"指数交易代理量",不是指数本身 |

---

## 13. 验收标准

- [ ] 4 个市场的 IndexCard 都显示价格 + 涨跌幅 + 5m 小图
- [ ] A 股 / 港股 IndexCard 显示资金净流入行
- [ ] 4 个市场都显示成交额行(美股用 ETF 代理量)
- [ ] 同比字段在第 2 个交易日开始正确显示
- [ ] 任一新字段失败,卡片其他部分仍正常显示
- [ ] `grep -rn "ak_call" apps/api/` 仍只命中注释(api 0 ak_call 红线不破)
- [ ] `pytest -m "not integration" -q` 全部 pass
- [ ] CLAUDE.md / docs/TODO.md 同步更新

---

## 14. 后续 Plan 拆分预告

预期切成 3-4 个 Plan(实际由 writing-plans skill 决定):
1. **Plan A**: 基础 schema + SQLite 表 + A 股 refresh 扩展 + 持久化 cron(可独立验证)
2. **Plan B**: 港股 collector job 实装(替换 stale 兜底)
3. **Plan C**: 美股 + Crypto 新 job
4. **Plan D**: 前端 IndexCard 渲染 + 格式化 + 文档同步

每个 Plan 独立可上线,前端 D 必须最后(等所有后端字段都有)。
