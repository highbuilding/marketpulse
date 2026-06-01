# 实时 K 线推送 + 分时图设计

> 让 A 股 / 美股的 K 线对齐 crypto 的实时体验(进行中态实时跳 + 收线即 push),并新增券商口径的分时图(时分线)。

- **日期**:2026-06-01
- **作者**:zhonghuai + Kiro
- **状态**:设计已对齐,待落实施计划
- **范围**:A 股先行验证 → 美股复刻;crypto 维持现状(已是标杆,不动)

---

## 第 0 章 · 背景与现状落差

### 0.1 现状(2026-06-01 实测确认)

三市场实时源**已存在**(CLAUDE.md 2026-05-30 那段"美股/A股无实时推送"已过时,本设计落地后需更正):

| 市场 | 实时源 | WS 原生周期 | 进行中态(final=false) |
|---|---|---|---|
| A 股 | `bar_poller` sina 10s 轮询 | 轮询 1m/5m/15m/30m | ❌ 只发 final=true |
| 美股 | `ws_consumer` Alpaca WS | 仅 1m | ❌ 只发 final=true |
| crypto | `ws_consumer` Binance WS | 8 周期全推 | ✅ 发 + 写 `:current` |

### 0.2 核心落差(本设计要消除的)

1. **A股/美股无 K 线进行中态**:最右那根未收线 bar 不跳动,要等收线才"啪"出现一根。crypto 靠 Binance WS 每周期推 `final=false` 实现实时跳。
2. **派生周期收线后既不及时也不 push**:15m/30m/60m/4h/1wk/1mo 靠 `sweep_derived` 每 30min 聚合一次,且聚合完**只写 DuckDB 不发 bus**(`aggregate_derived.py` 全文无 xadd)。前端只能 SWR 轮询主动拉,违背"被动 push"诉求。
3. **1m 是假数据**:`flush_quotes_to_duckdb` 把 quote 拍成 `open=high=low=close=price` 的伪 bar,无意义,要砍。
4. **无分时图**:券商那种当日逐分钟价格折线 + 均价线,项目完全没有。

### 0.3 目标(锁定需求)

**线一 · K 线(5m 及以上)**
- 5m/15m/30m **源头直取**(sina / Alpaca REST);60m/4h/1wk/1mo **聚合派生**
- **每个周期进行中态实时跳**(对齐 crypto):进行中推 `final=false` + 写 `:current`,**不入库**;收线才 `final=true` 入库
- **所有周期收线即发 bus → SSE 被动 push**(含聚合周期)
- 聚合从"30min 定时"改"源头收线事件驱动",`sweep_derived` 降频退为兜底
- 砍掉 1m 周期
- A 股先做并验证,再复刻美股(美股 IEX 不准/跳动大可接受)

**线二 · 分时图(时分线)**
- 券商效果:当日逐分钟价格折线 + 黄色均价线(累计成交额 ÷ 累计成交量)
- A 股 + 美股都做(先 A 股);crypto **不做**
- 分时数据保留 3 个月(约 60 交易日),独立物理隔离存储
- 分时也走 SSE 推送(A 股受 sina quote 限制实为 10s 粒度,非真秒级)

**crypto**:维持现状,完全不动(已是标杆;进行中态只推不入库现状已符合)。

### 0.4 设计原则对照(CLAUDE.md 第 0 章五条)

- **开源免费优先**:不引入付费 SIP;美股实时用免费 IEX。
- **优雅降级不 Fail-Fast**:进行中态丢一帧无所谓;聚合失败不拖累收线入库;collector 不可达分时历史降级 stale。
- **国内可用**:A 股 quote 走 sina 批量。
- **决策支持非执行**:仅展示,不交易。
- **单一可跑**:不引入新中间件,复用 Redis bus + DuckDB + 现有 SSE 通道。

---

## 第 1 章 · 整体架构

整个工作分**两条相对独立的线**,共用已有的 `bus` / `SSE` / `:current` 通道。crypto 不在改动范围内。

### 1.1 线一:K 线进行中态 + 收线即 push

```
源头采集(收线) ──final=true──┐
                            ├──> bus:bars.updated ──> SSE /sse/bars ──> 前端 K 线
quote_bar_ticker(进行中)─false─┘         ▲
       │                                 │
       └─> cache:bars:*:current ─────────┘ (SSE init 快照)

5m 收线 ──触发──> 事件驱动聚合(15m/30m/60m/4h) ──新桶收线──> bus(final=true)
1d 收线 ──触发──> resample 聚合(1wk/1mo)        ──新桶收线──> bus(final=true)
```

### 1.2 线二:分时图(时分线)

```
quote(A股 sina 10s / 美股 IEX) ──> intraday_line_writer ──┐
                                                          ├─> 独立分时库 intraday_{market}.duckdb
                                  └─> bus:intraday.updated ─┘         │
                                            │                         │
前端分时折线 <── SSE /sse/intraday <─────────┘                         │
前端首屏/历史 <── API /intraday-line <── collector 转发读 <───────────┘
                                          (90 天 purge cron)
```

### 1.3 组件清单

**线一 — 新增:**
1. `quote_bar_ticker`(A股 collector)— quote 驱动**所有被订阅周期**的进行中 bar,推 `final=false` + 写 `:current`,**不入库**。
2. **事件驱动聚合触发器** — 源头 5m/1d 收线时立即聚合受影响大周期,新收线桶发 bus。

**线一 — 改造现有:**
3. `bar_poller`(A股)— 砍 1m 轮询;5m/15m/30m 源头直取保留;收线发 `final=true`;最后一根(进行中)交给 ticker 不碰。
4. `aggregate_derived` — 聚合产出**新收线桶**时发 `bus:bars.updated`(final=true)。
5. `sweep_derived` — 降频(30min→2h),退为兜底 + 全量初始化。
6. `flush_quotes_to_duckdb` — 砍掉 quote→1m 伪 bar。
7. 美股 collector(`ws_consumer` + 聚合)— 同样改造,后做。

**线二 — 新增(全新,与 K 线零耦合):**
8. 独立分时库 `intraday_{market}.duckdb` + `IntradayLineRepo`(含 `purge_before`)。
9. `intraday_line_writer`(collector)— quote 驱动写当日逐分钟点(price + cum_amount + cum_volume + avg_price)+ 发 `bus:intraday.updated`。
10. 分时 purge cron — 每日砍 90 天前。
11. API `/api/sse/intraday/{symbol}` + `/api/symbols/{s}/intraday-line` + 前端折线组件 + EventSource hook。

**废弃:**
- 1m K 线周期(`INTERVAL_CONFIG` 移除或标记废弃)。
- quote→1m 伪 bar 逻辑。

---

## 第 2 章 · 线一数据流(K 线进行中态 + 收线 push + 事件驱动聚合)

线一最复杂,核心是**三个组件按"桶是否收线"严格分工,同一根 bar 绝不被两个组件同时处理**。

### 2.1 进行中态:`quote_bar_ticker`

**职责**:维护**所有被订阅周期**的"当前未收线桶",推 `final=false` + 写 `:current`,**不入库**。

**触发**:每 10s(跟 quote 节奏),对每个被 SSE 订阅的 `(symbol, interval)` 处理一次。订阅来源复用现有 `state:subscribe:ashare:{symbol}:{interval}`(SSE 连上写,断开过期),扫描方式同 `bar_poller._scan_subscriptions`。

**单次处理逻辑**:
1. 读最新 quote(`cache:quote:ashare:{symbol}`,含 price + 累计 volume)。
2. 用 `market_sessions.bucket_grid(market, date, interval_min)` 算当前价落进哪根桶 → `(open_ts, close_ts]`。
3. 维护该桶进行中 bar:`high=max`、`low=min`、`close=price`、`open`=桶内首个 quote 价。状态存 collector 进程内存(per symbol+interval)。
4. 写 `cache_bars_current` + 发 `bus:bars.updated`(`final=false`)。
5. **不入库**。

**进行中桶 OHLC 基线问题(关键)**:桶可能在 collector 启动前就开始(如 13:20 重启,60m 桶 13:00 已开)。ticker 内存里 open/high/low 不全。处理:**首次为某周期建当前桶时,先从已收线的更小周期 bar 算出该桶到目前为止的 OHLC 基线**(如 60m 当前桶 = 已收线的 13:00-13:50 各 5m 聚合 + 13:50 后 quote),再叠 quote。避免重启/中途订阅时 open 漂移。

### 2.2 收线入库:源头采集(唯一写库 + 发 final=true 处)

- **5m/15m/30m**:`bar_poller` 每 10s 拉 sina 当日全序列。**已收线的根**(非最后一根)→ upsert DuckDB + 发 `final=true`;**最后一根(进行中)交给 ticker,poller 不发它**。
- **1d**:`fetch_intraday_job` 收盘后拉 → 入库 + 发 `final=true`。
- **不重叠保证**:ticker 只处理"当前未收线桶"(close_ts > now),poller 只处理"已收线桶"(close_ts ≤ now)。同一根 bar 在某一刻只属于一方。

### 2.3 事件驱动聚合:源头收线触发大周期

- 2.2 中 **5m 收线那一刻**,立即聚合 15m/30m/60m/4h 受影响的桶(复用 `aggregate_derived_for_symbol`)。
- 聚合出的桶**已收线**(close_ts ≤ now)→ 入库 + 发 `final=true`;**未收线**(close_ts > now)→ 什么都不做(交给 ticker 跳)。
- **1d 收线** → 触发 1wk/1mo resample,同样只对已收线桶发 final=true。

**时序示例(13:50 这根 5m 收线)**:13:45-13:50 收线 → 触发聚合 → 算 15m 桶 13:45-14:00(close=14:00 > now)、30m 桶 13:30-14:00、60m 桶 13:00-14:00 → **全未收线,聚合空转,不发不入库**。这些大桶的实时跳由 ticker 负责。直到 **14:00 这根 5m(13:55-14:00)收线** → 再触发聚合 → 15m 13:45-14:00、30m 13:30-14:00、60m 13:00-14:00 全部 close=14:00 ≤ now → 入库 + 发 final=true。

> **关键不变量**:大周期桶边界一定落在 5m 网格上(15m/30m/60m 边界 13:45/14:00 都是 5m 收线点),所以"边界那根 5m 收线"必然触发大桶收线,不会漏。

### 2.4 三者职责边界(一句话)

| 组件 | 管什么 | 动作 |
|---|---|---|
| `quote_bar_ticker` | 所有周期的**当前未收线桶** | `final=false` + `:current`,**不入库** |
| 源头采集(poller/fetch) | 5m/15m/30m/1d 的**已收线桶** | 入库 + `final=true`,并触发聚合 |
| 事件驱动聚合 | 60m/4h/1wk/1mo 的**已收线桶** | 入库 + `final=true` |

### 2.5 鲁棒性兜底

- **边界 5m 迟到/丢失** → 下一根 5m 收线重算最近窗口,补发大桶 final=true(晚几分钟不丢)。
- **极端缺口/重启** → `sweep_derived` 降频(2h)兜底 + 全量初始化新标的。
- **进行中态丢帧** → 无所谓,下一轮 quote 补;收线由源头采集保证入库。

### 2.6 美股差异(后做)

- 美股 ticker 数据源:盘中 quote 来自 `tick:us`(Alpaca latest_quote 10s);进行中态精度受 IEX 限制,可接受。
- 美股 5m/15m/30m:改 Alpaca REST 源头直取(`fetch_intraday` 已支持这些 freq),替代当前"WS 1m + 聚合 5m"。
- 收线 + 聚合 + 发 bus 逻辑与 A 股一致,复用同一套聚合触发器。

---

## 第 3 章 · 线二数据流(分时图 / 时分线)

线二与 K 线**零耦合**:独立库、独立 bus channel、独立 SSE 端点、独立前端组件。

### 3.1 存储:独立库 `intraday_{market}.duckdb`(物理隔离)

**为何单开文件而非现有 bars 库新表**(雷区 6):`intraday_line_writer` 每 10s(SSE 后更频繁)高频写,若塞进 `bars_{market}.duckdb` 会和 K 线写抢同一 RW 连接(DuckDB 同库同时刻仅一个 RW 连接独占)。单开文件 → 分时有独立 RW 连接,与 K 线物理隔离,零锁竞争。符合项目"按市场分文件"底盘约定。

`IntradayLineRepo`(`core/persistence/intraday_repo.py`),表 `intraday_lines`:

| 字段 | 含义 |
|---|---|
| symbol | 标的 |
| ts | 该分钟时刻(UTC) |
| price | 该分钟最新价 |
| cum_amount | 当日累计成交额(到该分钟) |
| cum_volume | 当日累计成交量(到该分钟) |
| avg_price | 均价 = cum_amount / cum_volume(写入时算好,前端零计算) |

主键 `(symbol, ts)`,ON CONFLICT 覆盖(同一分钟内多次 quote 覆盖,得该分钟末值)。方法:`insert_points` / `fetch_day(symbol, date)` / `purge_before(cutoff)`。

### 3.2 写入:`intraday_line_writer`(collector)

- 每 10s(跟 quote 节奏),对每个**被订阅**的分时标的:读 `cache:quote:ashare:{symbol}`。
- A 股 sina quote 累计成交额在 `parts[9]` → 需在 `_fetch_snapshot_sina` 把累计成交额带进 `Quote`(现 Quote 模型无 amount 字段,要加 `amount`)。
- 算 `avg_price = cum_amount / cum_volume`,以"当前分钟"(ts 截断到分钟)为主键 upsert 一行。同一分钟内每 10s 一次 quote 反复覆盖该行,收盘得该分钟末值。**DB 存储粒度=分钟;SSE 推送频率=10s**(每次 quote 都推当前分钟点的最新值,前端实时更新最右点)。两者不矛盾。
- 只在交易时段写(复用 `is_market_session_open`)。
- 写库后发 `bus:intraday.updated`(payload: symbol/ts/price/avg_price)。
- 失败仅 warning,不影响 quote 主流程。

### 3.3 推送:独立 SSE 端点

- **新增 bus channel** `bus:intraday.updated`(`core/cache/keys.py` 加常量,符合规范 2)。**不复用 `bus:bars.updated`**(避免与 K 线 tick 混流、SSE 按 interval 过滤错乱)。
- **新增 SSE 端点** `GET /api/sse/intraday/{symbol}`(`apps/api/routes/sse_intraday.py`),订阅 `bus:intraday.updated`,按 symbol 过滤推前端。结构复刻 `sse_bars.py`。
- A 股粒度:受 sina quote 10s 刷新限制,实为 **10s 粒度推**(非真秒级),但"推"而非"拉",体验顺。美股 IEX 可更快。

### 3.4 读取 + 前端

- **历史/首屏**:`GET /api/symbols/{symbol}/intraday-line?date=today`(默认当日,可传历史交易日)。api 不直连 DuckDB(雷区 6)→ **走 collector 转发**:collector 内嵌 `/internal/intraday-line` 同进程查 `IntradayLineRepo`,api httpx 转发(复用 `attach_*_route` + `trust_env=False` 模式)。collector 不可达 → 降级 `stale=true`。
- **前端**:分时折线组件首屏拉当日全量(A 股 240 点),再用 EventSource 订阅 `/api/sse/intraday/{symbol}` append/更新最右点。价格折线读 `price`,均价线读 `avg_price`。

### 3.5 清理:purge cron

- 新增 collector 每日 cron(凌晨),调 `IntradayLineRepo.purge_before(now - 90d)` 删 90 天前分时行。
- `purge_before` 为 `IntradayLineRepo` 独有的全新实现(`DELETE FROM intraday_lines WHERE ts < ?`),只动分时库,不碰 `BarRepo` / K 线库(K 线不做留存上限)。

### 3.6 美股分时差异(后做)

- 美股 quote 来自 `tick:us`(Alpaca latest_quote);累计成交额若 Alpaca latest_quote 不含,则用当日累计 bar volume 估算或留空(均价线降级)。
- 先验证 A 股分时,再复刻美股。

---

## 第 4 章 · 错误处理、不变量与测试

### 4.1 优雅降级(贯穿原则 2)

- **ticker**:quote 读不到 / 桶计算失败 → 该 (symbol,interval) 跳过本轮,仅 warning,不影响其他。进行中态丢一帧无所谓,下一轮补。
- **事件驱动聚合**:某周期聚合抛错 → 单周期 try/except,不阻塞其他周期 + 不拖累 5m 收线入库(聚合是"附加动作")。
- **分时 writer**:写库 / 发 bus 失败 → warning,不影响 quote 主流程。
- **collector 不可达**:分时历史走转发,collector 挂 → api 降级 `stale=true`(复用雷区 6 模式)。

### 4.2 关键不变量(测试守护)

1. **进行中桶与收线桶不重叠**:同一 (symbol,interval) 同一根 bar,绝不同时被 ticker 发 `final=false` 又被采集发 `final=true`。判定:`close_ts ≤ now` 才算收线。
2. **收线只入库一次**:源头采集 + 聚合对同一桶幂等(DuckDB ON CONFLICT 保证;测重复触发不产生脏数据)。
3. **大周期当前桶 OHLC 正确**:ticker 建桶时用更小周期 bar 补基线,重启/中途订阅后 open 不漂移。

### 4.3 测试分层(复用规范 7)

- **单元**:`bucket_grid` 桶边界、ticker OHLC 攒法、聚合"是否收线"判定、`avg_price` 计算、`purge_before` 砍对日期。纯函数 / mock quote / `fakeredis`。
- **集成**(`@pytest.mark.integration`,默认不跑):quote→ticker→bus→SSE 端到端推一帧;5m 收线→触发 15m 聚合→发 bus。
- **回归 fixture**:固化 quote 序列喂 ticker,断言进行中桶 OHLC 序列符合预期(不依赖网络)。
- **Playwright 证据式验证**(memory `feedback_playwright_evidence_testing`):A 股盘中驱动真实 Chrome,拦 SSE 网络流当证据,确认分时折线 + K 线进行中桶都在跳。

### 4.4 验证落地(雷区 2 模板)

改完按 CLAUDE.md 三步:后端 import 测试 + 前端 `tsc --noEmit` + `pytest -m "not integration"`,然后重启 3 collector + api 冒烟。任何 `pkill` 配套 nohup 重启(雷区 2 反模式)。

---

## 第 5 章 · 实施顺序(给 writing-plans 的切分提示)

按"可独立验证"切分,A 股先行:

1. **废弃 1m + 砍伪 bar**:`flush_quotes_to_duckdb` 去掉 quote→1m;`INTERVAL_CONFIG` 标记 1m 废弃;前端 interval tab 去 1m。
2. **聚合发 bus + 事件驱动**:`aggregate_derived` 收线桶发 `bus:bars.updated`;源头 5m/1d 收线挂聚合触发;`sweep_derived` 降频 2h。
3. **A 股进行中态 `quote_bar_ticker`**:新组件 + OHLC 基线补全 + 订阅扫描 + `:current` + final=false。
4. **A 股分时图(线二)**:`intraday_{market}.duckdb` + `IntradayLineRepo` + `intraday_line_writer` + `bus:intraday.updated` + SSE 端点 + 转发读 + purge cron + 前端折线组件。
5. **美股复刻**:ws_consumer 改 REST 源头直取 5m/15m/30m;美股 ticker;美股分时。
6. **更正 CLAUDE.md**:删"美股/A股无实时推送"过时段落,补本设计落地态。

每步交付后按 4.4 验证 + 提交(中文 commit,按主题拆)。

---

## 附录 · SSoT 影响清单(改这些单一事实源)

| 概念 | SSoT 位置 | 改动 |
|---|---|---|
| Redis key 命名 | `core/cache/keys.py` | 加 `BUS_INTRADAY_UPDATED` + 分时 current key |
| Interval 元数据 | `core/domain/intervals.py` | 1m 标记废弃 |
| 前端 Interval tab | `apps/web/lib/intervals.ts` | 去 1m |
| 桶网格 | `core/domain/market_sessions.py::bucket_grid` | 复用(ticker 算桶) |
| 聚合 | `apps/collector/jobs/aggregate_derived.py` | 加发 bus + 事件驱动入口 |
| 分时存储 | `core/persistence/intraday_repo.py`(新) | 新建 |
| 分时取数(前端) | `apps/web/lib/use_intraday_line.ts`(新) | 新建 |
