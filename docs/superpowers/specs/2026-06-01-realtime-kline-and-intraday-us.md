# 美股实时 K 线 + 分时图设计(复刻 A 股 + 适配美股数据本质)

> 把 A 股已交付的"实时 K 线进行中态 + 券商口径分时图"两条线复刻到美股,适配美股 Alpaca/IEX 的数据本质差异。

- **日期**:2026-06-01
- **作者**:zhonghuai + Claude
- **状态**:设计已对齐(用户拍板),待落实施计划
- **范围**:美股复刻(对应 A 股 spec `2026-06-01-realtime-kline-and-intraday-line-design.md` 第 5 章步骤 5)。crypto / A 股不动。
- **前置**:A 股两条线已交付(`2026-06-01-realtime-kline-and-intraday-ashare.md` 实施计划步骤 1-4 已落地并提交)。

---

## 第 0 章 · 背景与三个数据本质差异

### 0.1 现状(2026-06-01 实测确认)

| 项 | A 股(已交付) | 美股(现状) |
|---|---|---|
| 实时源 | sina quote 10s 轮询 → `quote_bar_ticker`(quote 驱动进行中态) | Alpaca WS **`bars` 频道(1m 粒度)**,`ws_consumer.py:190` 订阅 `"bars"` |
| 进行中态 | ✅ `final=false` + `:current` | ❌ WS 推 1m `final=true`,无进行中态 |
| 分时图 | ✅ quote 累计成交额 → 价格线 + 均价线 | ❌ 完全没有 |
| 1m bar | ❌ 已废弃,不入库 | ⚠️ **`ws_consumer.py:128` 每根 1m 落 `bars_us.duckdb`,但无任何消费者**(孤儿数据) |

### 0.2 美股三个硬差异(决定方案必须偏离 A 股)

1. **WS 当前是 1m 不是秒级**:`ws_consumer.py:190-192` 订阅 `bars` 频道,Alpaca 每分钟收线后推一根 1m OHLCV(注释 `:3` 自承"原生只推送 1m bar")。要做"秒级实时"必须换频道。
2. **quote 无成交量/成交额**:`core/adapters/us.py:87` `_fetch_snapshot_alpaca` 直接 `volume=0`(只取 bid/ask 中间价);yfinance 兜底的 `last_volume` 也只是最后一笔。**A 股那套 quote 驱动分时(累计成交额)在美股零数据,搬不过来**。
3. **IEX 免费层 vs SIP**:实时只能用 IEX(WS,秒级,但成交量只含 IEX 一个交所约几 %);权威成交量在 SIP(REST `fetch_intraday` feed="sip",但免费层延迟 ~15-20min,`us.py:141` `end_safe = now - 20min`)。**权威性与实时性不可兼得**。

### 0.3 关键决策(用户已拍板)

1. **WS 换 `trades` 频道**(逐笔成交,秒级):每笔成交 `p`(价)+`s`(量),喂满分时图(真 VWAP)+ 进行中态 K 线。**替代当前 `bars`(1m)频道**。
2. **收线 bar 走 REST SIP**(混合方案):DuckDB 只存 SIP 权威收线 bar(成交量准,喂 CD 信号/量指标);进行中态 + 分时走 IEX `trades` 实时跳。
3. **1m bar 不再落库**:对齐 A 股/crypto(`KLINE_INTERVALS` 本就不含 1m)。1m 维度彻底退出美股 K 线链路。
4. **分时仅 RTH**:券商分时只画正常交易时段(09:30-16:00 ET);盘前/盘后不写分时点。
5. **非 RTH 前端默认 K 线**:盘前/盘后/隔夜打开详情页默认 K 线视图(K 线含盘前盘后照常可取),分时 tab 仍在但点开提示"仅盘中"。
6. **昨收基准线**:券商分时那条灰色虚线(昨收)+ 价格相对昨收红绿染色,本次一并加上(A 股当前无,属增强)。

### 0.4 设计原则对照(CLAUDE.md 第 0 章五条)

- **开源免费优先**:实时用免费 IEX `trades`;不上付费 SIP 实时。
- **优雅降级不 Fail-Fast**:逐笔丢帧无所谓;SIP 收线迟到由 bus-only provisional 兜底;collector 不可达分时降级 stale。
- **国内可用**:美股不涉及国内通道。
- **决策支持非执行**:仅展示。
- **单一可跑**:复用 Redis bus + DuckDB + 现有 SSE 通道 + A 股已建的 `aggregate_and_publish` / `IntradayLineRepo` / `sse_intraday` / `attach_intraday_route`,不引入新中间件。

---

## 第 1 章 · 整体架构:`trades` 当唯一实时心跳

美股从"quote 驱动(A 股)"改为**逐笔事件驱动**:每笔 IEX trade 到达即分发给三个消费者。1m 概念彻底消失。

```
Alpaca IEX WS  ── trades(逐笔 p,s)──┐
  (换频道:bars→trades)             │  TradeHub 内存累加 + 节流(~1s)
                                    ├─→ ① IntradayLineWriter(仅 RTH)
                                    │      cum_amount+=p×s, cum_vol+=s, avg=cum_amount/cum_vol
                                    │      → intraday_us.duckdb(分钟粒度 upsert)
                                    │      → bus:intraday.updated + cache:intraday:us:*:current
                                    ├─→ ② BarTicker(所有被订阅周期)
                                    │      逐笔攒当前 5m/15m/30m/60m/4h 桶 OHLC(close=p, vol+=s)
                                    │      → final=false + cache:bars:us:*:current (不入库)
                                    │      桶滚动时对刚收桶补发 1 根 provisional final=true → bus ONLY(填洞,不入库)
                                    └─→ (1m 不落库 / 不发 1m SSE / 不进 tail)

REST SIP 轮询(收线源, 权威成交量, ~20min 延迟)
  BarPoller: fetch_intraday 5m/15m/30m 已收线根
    → upsert bars_us.duckdb + 发 final=true(按 ts 覆盖前端 provisional)
    → 5m 收线触发 aggregate_and_publish(60m/4h)  [复用 A 股已建入口]
```

**职责不重叠**(同 A 股不变量):BarTicker 只管 `close_ts > now` 当前桶;BarPoller 只管 `close_ts ≤ now` 已收线桶。唯一交叠是"桶滚动 provisional final=true"——它**只发 bus 不入库**,DuckDB 永远只有 SIP 权威收线 bar。

### 1.1 IEX 量偏小的取舍(已接受)

`trades`(IEX)成交量只含 IEX 一个交所(约占全美几 %)。影响:
- **分时价格线**:逐笔成交价,与全市场一致 ✓
- **分时均价线(VWAP)**:基于 IEX 子集成交,价格代表性 OK,绝对量偏小
- **分时量柱 / 进行中态桶 volume**:偏小(仅 IEX)
- **收线 bar volume**:**不受影响**——走 REST SIP,全市场权威量,喂 CD 信号/量指标

UI 在分时图加注脚标注"成交量为 IEX 口径"(原则 1 免费层取舍)。CD 信号/量指标读 DuckDB(SIP),零污染。

---

## 第 2 章 · 组件清单

### 2.1 复用(市场无关,零改或仅传参)

| 组件 | 复用方式 | 验证 |
|---|---|---|
| `core/persistence/intraday_repo.py::IntradayLineRepo` | 指向 `data/intraday_us.duckdb` | A 股已建,含 purge_before |
| `core/cache/keys.py` | `cache_intraday_current(market,...)` / `BUS_INTRADAY_UPDATED` / `cache_bars_current(market,...)` 已带 market 参 | 已存在 |
| `apps/api/routes/sse_intraday.py` | 按 `infer_market` 推送,`infer_market("AAPL")="us"` | ✅ 实测通过 |
| `apps/api/routes/symbols.py::intraday_line` | `collector_base_url("us")=8789` 转发 | ✅ 实测 deps.py:81 |
| `apps/collector/base.py::attach_intraday_route` | US main 直接调 | 通用 |
| `apps/collector/jobs/aggregate_derived.py::aggregate_and_publish` | 5m 收线触发,market 传 "us" | A 股已建 |
| 前端 `lib/use_intraday_line.ts` / `components/IntradayLineChart.tsx` | 按 symbol 通用,page.tsx `supportsIntraday` 已含 'us' | commit 6167bce |
| 前端 K 线 SSE `use_kline_stream` / `sse_bars` | US 发 `final=false/true` 后通用 | 通道已通 |

### 2.2 纯函数提取复用(从 ashare 提到共享处)

A 股 `apps/collector/ashare/quote_bar_ticker.py` 里的市场无关纯函数,提到共享模块(建议 `core/domain/bucket_state.py` 或 `apps/collector/_shared/`),A 股 + 美股共用:
- `BucketState` + `update_bucket`(OHLC 攒法)
- `current_bucket(market, now, interval_min)`(当前桶定位)
- `seed_baseline(bars)`(更小周期补基线)

提取后 A 股 `quote_bar_ticker.py` import 共享,不改行为(回归测试守护)。

### 2.3 新建(美股专属,`apps/collector/us/`)

| 文件 | 职责 |
|---|---|
| `trade_hub.py` | WS `trades` 逐笔接收 → 内存累加器(per symbol: cum_amount/cum_vol/last_price/session_date)+ 节流(~1s)分发给 writer + ticker。RTH 开盘重置累加器 |
| `intraday_line_writer.py` | 消费 TradeHub 节流事件,算 VWAP,分钟粒度 upsert `IntradayLineRepo` + 发 `bus:intraday.updated` + `:current`。仅 RTH |
| `bar_ticker.py` | 消费 TradeHub 节流事件,逐笔攒进行中桶,推 `final=false` + `:current`;桶滚动补发 provisional `final=true`(bus only)。复用 2.2 纯函数 |
| `bar_poller.py` | REST SIP 周期轮询 5m/15m/30m 已收线根 → upsert + `final=true` + 触发 `aggregate_and_publish(60m/4h)` |

### 2.4 改造

| 文件 | 改动 |
|---|---|
| `apps/collector/us/ws_consumer.py` | 订阅 `bars`→`trades`;`_parse_bar`→`_parse_trade`(取 `p`/`s`/`t`);删 1m 三写 `handle_bar`,改分发 TradeHub。重连退避逻辑保留 |
| `apps/collector/us/main.py` | 接线 TradeHub / writer / ticker / bar_poller / intraday_repo / `attach_intraday_route` / 分时 90d purge cron / sweep 降频(30→120min) |
| `apps/web/app/symbol/[code]/page.tsx` | 非 RTH 默认 `viewMode='kline'`;分时 tab 非 RTH 点开提示"仅盘中(RTH)" |
| `apps/web/lib/markets.ts` | 加 `isUsRegularSession(now)`(09:30-16:00 ET 判定,镜像后端) |
| `apps/web/components/IntradayLineChart.tsx` | 加昨收基准线(灰虚线)+ 相对昨收红绿染色;IEX 量注脚 |
| `apps/web/lib/use_intraday_line.ts` | 响应带 `prev_close` 字段 |

### 2.5 废弃/清理

- 美股 1m bar 不再写 `bars_us.duckdb`(ws_consumer 改造后自然停止)。
- 存量孤儿 1m bar:可选一次性 `DELETE FROM bars WHERE interval='1m'`(`bars_us.duckdb`),非必须。
- `core/services/kline_service.py::_get_one_minute_bars`(55s 内存缓存)若确认无前端调用,标废弃(本计划不强制,grep 确认后另议)。

---

## 第 3 章 · 数据流细节

### 3.1 TradeHub:逐笔接收 + 累加 + 节流

**Alpaca trade 消息**:`{"T":"t","S":"AAPL","p":150.25,"s":100,"t":"2026-06-01T14:30:00.123Z",...}`。

**内存状态(per symbol)**:
```
TradeAccumulator:
  session_date: date        # 当前 RTH 交易日(ET)
  cum_amount: float         # Σ(p×s) since RTH open
  cum_volume: int           # Σ(s) since RTH open
  last_price: float
  bucket_states: dict[interval -> BucketState]  # 进行中桶(给 ticker)
```

**逐笔处理**:
1. 跨日/RTH 开盘检测:`trade.ts` 的 ET 日期 ≠ `session_date` 或首次进入 RTH → 重置 `cum_amount=cum_volume=0`,清 bucket_states。
2. 累加 `cum_amount += p×s`、`cum_volume += s`、`last_price = p`。
3. 更新各被订阅周期当前桶 OHLC(`close=p`,`volume += s`,`high/low`)。
4. **不立即推送**——标记 dirty。

**节流(~1s tick)**:每 1s 对 dirty 的 symbol 各推一次:
- writer:取累加器算 `avg = cum_amount/cum_volume`,分钟粒度点 upsert + 发 bus。
- ticker:取各桶状态推 `final=false` + `:current`。

**存储粒度=分钟,推送频率=1s**:库里每分钟一个分时点(同分钟内反复 upsert 取末值,240 点/日);SSE 每 1s 推最新值(前端实时刷最右点)。两者解耦,同 A 股口径。

### 3.2 进行中态桶基线(重启/中途订阅)

桶可能在 collector 启动前就开始(13:20 启动,60m 桶 13:00 已开)。BarTicker 建当前桶时,用已收线的更小周期 bar(REST SIP 已存的 5m)算 OHLC 基线(`seed_baseline`),再叠逐笔。避免 open 漂移。同 A 股逻辑。

### 3.3 桶滚动 provisional(填 SIP ~20min 洞)

REST SIP 延迟 ~20min,"最后一根 SIP 收线 bar"与"当前进行中桶"间会空出几根已收桶。解决:
- BarTicker 检测到当前桶 `close_ts ≤ now`(已滚动到下一桶)时,对**刚收的那根桶发 1 次 `final=true` 到 `bus:bars.updated`**(IEX 量,provisional)。
- **只发 bus,不写 DuckDB**。前端按 ts 接收填洞。
- ~20min 后 BarPoller 的 REST SIP 落库权威版,发 `final=true`(同 ts),前端覆盖 provisional 的 IEX 量为 SIP 权威量。
- DuckDB 永远只有 SIP 权威收线 bar → CD 信号/量指标零污染。

### 3.4 BarPoller:REST SIP 收线源

- 每 ~60-90s 对被订阅美股标的调 `fetch_intraday(symbol, freq)`(5m/15m/30m)。
- 新出现的已收线根(`ts ≤ end_safe`,DuckDB 无)→ upsert + 发 `final=true`。
- 5m 收线 → `aggregate_and_publish(repo, redis, "us", symbol, targets=("60m","4h"), now=...)`(复用 A 股入口)。
- 仅交易日跑(`is_trading_day("us")`),含盘前盘后 session(`market_sessions` us 三段)。
- 失败仅 warning,不阻塞(原则 5)。

### 3.5 分时仅 RTH + 盘前盘后 K 线

- **分时 writer**:`is_us_regular_session(now)` 为真才写(09:30-16:00 ET)。盘前/盘后逐笔只喂 K 线 ticker,不写分时点。
- **K 线**:`bucket_grid("us", date, n)` 本就覆盖盘前/RTH/盘后三段(`market_sessions.py:24`),ticker + poller 盘前盘后照常产出 K 线桶。盘前 K 线可取(用户要求)。

### 3.6 前端非 RTH 默认 K 线 + 昨收线

- `page.tsx`:`effectiveMarket==='us' && !isUsRegularSession(now)` → 初始 `viewMode='kline'`。
- 分时 tab 始终渲染;非 RTH 点击不切到空分时,而是提示"分时仅盘中(09:30-16:00 ET)"。
- `IntradayLineChart`:新增昨收基准线(`prev_close`,灰色 `LineStyle.Dashed`)+ 价格相对昨收红绿(高于昨收红/低于绿,A 股口径)。`prev_close` 来源:`/intraday-line` 响应带(collector 内嵌路由查当日前一交易日 1d 收盘,或 BarPoller 启动缓存)。

---

## 第 4 章 · 错误处理、不变量与测试

### 4.1 优雅降级(原则 2)

- **TradeHub/ticker/writer**:单 symbol 处理抛错 → 跳过本轮,warning,不影响其他。逐笔丢帧无所谓,下一笔补。
- **BarPoller**:单 symbol fetch 失败 → warning,不拖累其他 + 不阻塞聚合(单条 try/except)。
- **WS 断线**:指数退避 reconnect(1s→60s),保留现有逻辑。
- **collector 不可达**:分时历史走转发,collector 挂 → api 降级 `stale=true`(复用雷区 6)。
- **无 Alpaca key**:WS 不启动,仅 warning(现状保留)。

### 4.2 关键不变量(测试守护)

1. **进行中桶与收线桶不重叠**:ticker 只发 `close_ts > now` 当前桶 final=false;BarPoller 只发 `close_ts ≤ now` final=true。provisional 是唯一例外(bus only,幂等)。
2. **DuckDB 只有 SIP 权威收线 bar**:ticker/provisional 绝不 `insert_bars`;只有 BarPoller 写库。`grep insert_bars apps/collector/us/{trade_hub,bar_ticker,intraday_line_writer}.py` 应为空。
3. **VWAP 正确**:`avg = Σ(p×s)/Σ(s)`,RTH 开盘重置累加器。
4. **1m 不入库**:改造后 `bars_us.duckdb` 不再新增 interval='1m' 行。
5. **大周期当前桶 OHLC 正确**:ticker 建桶用 5m 补基线,重启/中途订阅 open 不漂移。

### 4.3 测试分层(规范 7)

- **单元**:trade 累加器(跨日重置 / VWAP / 节流取末值)、`update_bucket`/`seed_baseline`(共享纯函数回归)、provisional 触发判定(桶滚动)、`is_us_regular_session`、BarPoller 已收线判定。纯函数 / mock trade / fakeredis。
- **集成**(`@pytest.mark.integration`,默认不跑):trade→hub→bus→SSE 端到端推一帧;5m 收线→`aggregate_and_publish`→发 bus。
- **回归 fixture**:固化 trade 序列喂 TradeHub,断言 VWAP + 进行中桶 OHLC 序列符合预期(不依赖网络)。
- **Playwright 证据式验证**(memory `feedback_playwright_evidence_testing`):美股盘中驱动真实 Chrome,拦 `/api/sse/bars` 与 `/api/sse/intraday` 网络流,确认:① K 线进行中桶 final=false 在跳;② 分时折线 + 均价线 + 昨收线在更新。盘前打开默认落 K 线。

### 4.4 验证落地(雷区 2 模板)

改完按 CLAUDE.md 三步:后端 import 测试 + 前端 `tsc --noEmit` + `pytest -m "not integration"`,然后重启 3 collector + api 冒烟。任何 `pkill` 配套 nohup 重启(雷区 2 反模式)。重点只重启 `apps.collector.us.main` + api(3 进程隔离)。

---

## 第 5 章 · 实施顺序(给 writing-plans 的切分提示)

按"可独立验证"切分:

1. **共享纯函数提取**:`BucketState`/`update_bucket`/`current_bucket`/`seed_baseline` 从 ashare 提到共享模块,A 股 import 改向,回归测试守护(零行为变化)。
2. **WS 换 trades + TradeHub**:`ws_consumer` 订阅 + 解析改 trades;新建 `trade_hub.py`(累加器 + 跨日重置 + 节流)。先不接 writer/ticker,只验证逐笔进内存。
3. **美股 BarTicker**:消费 TradeHub,推 final=false + :current + 基线补全 + provisional 填洞。接线 main。
4. **美股分时图(线二)**:`intraday_line_writer.py`(VWAP)+ `intraday_us.duckdb` repo 接线 + `attach_intraday_route` + 90d purge cron + 前端昨收线/染色/IEX 注脚。
5. **美股 BarPoller(SIP 收线)**:REST SIP 周期轮询 5m/15m/30m 收线入库 + final=true + 触发聚合;sweep 降频 2h。
6. **前端非 RTH 默认 K 线**:`isUsRegularSession` + page.tsx 默认视图 + 分时 tab 盘前提示。
7. **更正 CLAUDE.md**:删"美股/A股无实时推送"过时段落,补两市场实时落地态 + 美股 trades/SIP 混合架构。

每步交付后按 4.4 验证 + 提交(中文 commit,按主题拆)。

---

## 附录 · SSoT 影响清单

| 概念 | SSoT 位置 | 改动 |
|---|---|---|
| 共享桶纯函数 | 新建 `core/domain/bucket_state.py`(或 `_shared`) | 从 ashare 提取 |
| US WS 频道 | `apps/collector/us/ws_consumer.py` | bars→trades |
| US 实时分发 | `apps/collector/us/trade_hub.py`(新) | 新建 |
| US 进行中态 | `apps/collector/us/bar_ticker.py`(新) | 新建 |
| US 分时 writer | `apps/collector/us/intraday_line_writer.py`(新) | 新建 |
| US 收线源 | `apps/collector/us/bar_poller.py`(新) | 新建 |
| 美股 RTH 判定(前端) | `apps/web/lib/markets.ts::isUsRegularSession` | 新增 |
| 分时图昨收线 | `apps/web/components/IntradayLineChart.tsx` | 加 prev_close 线 + 染色 |
| 分时取数(前端) | `apps/web/lib/use_intraday_line.ts` | 响应加 prev_close |
