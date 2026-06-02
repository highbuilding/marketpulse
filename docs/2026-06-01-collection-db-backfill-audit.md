# MarketPulse 全工程审计:采集 / 入库 / DB / 前端拉取 / SSE / 启动回填

> 一次跨三采集进程 + DB + 前端 + 启动链路的只读深度审计,定位数据完整性与健壮性缺陷,给出 bug 清单与修复方案。

- **日期**:2026-06-01
- **审计人**:zhonghuai + Claude(4 路并行只读研究 + 源码核实 + 实库查询)
- **范围**:ashare(8788)/ us(8789)/ crypto(8790)三采集进程,bars_*/intraday_*/state DB,apps/web 前端拉取与 SSE,collector 启动与回填
- **前置假设(本审计基于)**:当前架构为**单机 + 单人本地工具**(CLAUDE.md 第 0 章原则 4/5)。多用户 / VPS 部署的扩展性评审另立文档。
- **状态**:结论已核实;部分 bug 本次会话已修(标 ✅),其余按优先级待修。

---

## 0. 结论速览(TL;DR)

- **采集矩阵**:三市场每周期都有源(直取或派生),无"空洞周期"。但 **A 股 15m/30m 双源(sina 直取 + 5m 聚合)存在覆盖竞态**;美股 15m/30m 纯 REST 直取无此问题(两市场不对称)。
- **实时能力**:5m/15m/30m/60m/4h 三市场都能"未收线 K 线实时跳";**1d/1wk/1mo 在 A股/美股无进行中态**(crypto 有,不对等)。频率:crypto 秒级 WS / 美股 1s / A股 10s。
- **DB**:`bars.duckdb`(72M)+ 3 个 `.before-*` 备份(115M)= **~187M 遗留可删**;`bars_us` 有 **2431 行 1m 孤儿**(泄漏源已堵,需守卫+清理);其余库可信;intraday 90 天 purge cron 已注册有效。
- **SSE 生命周期**:前端 cleanup 正确、后端 generator 断开正确释放,**无连接/协程泄漏**。
- **🔴 核心风险**:**美股收线采集无 baseline 兜底、绑 DB watchlist + 前端默认列表脱节** → 部分美股(现实例:TSLA/AMZN/META/AMD)可能从不被采集。
- **🔴 健壮性**:**A股/美股无冷启动 backfill、无 kill 后启动 reconcile**;派生周期"中段缺口"不重聚合;长断档永久空洞。
- **本次已修**:美股冬令时盘后桶错位(`current_bucket` 用本地日,commit `5c0b77c`);`state:subscribe` 生产者缺失(SSE 写订阅表,`2ca9ef5`,顺带激活 A 股实时进行中态);美股 trades 30 符号上限(动态订阅,`a76a0ac`);美股 1m 落库源(ws_consumer 换 trades,`04c7669`)。

---

## 1. 采集 + 入库矩阵(原始 vs 派生)

**项目周期集**:`5m / 15m / 30m / 60m / 4h / 1d / 1wk / 1mo`(`1m` 已废弃,见 §3.3)。

**入库通则**:所有市场**收线 bar(final=true)入库 DuckDB**;**进行中态(final=false)一律不入库**,只写 `cache:bars:{market}:{symbol}:{interval}:current`。

### 1.1 A 股(ashare)

| 周期 | 来源 | 入库 | 进行中态 | 频率 |
|---|---|---|---|---|
| 5m | 源头直取 sina `stock_zh_a_minute`(`bar_poller.py:36`)| ✅ 仅收线根 | ✅ quote_bar_ticker | ticker 10s;poller 10s |
| 15m / 30m | 源头直取 sina(`bar_poller.py:36`)— ✅ B5 已修:去掉 5m 聚合,单一来源 | ✅ | ✅ ticker | 10s |
| 60m / 4h | 聚合自 5m(`aggregate_derived.py:161-162`)| ✅ 聚合 | ✅ ticker | ticker 10s;5m 收线事件驱动 + sweep 120min |
| 1d | 源头直取 `stock_zh_a_daily`,cd:1d cron BJT15:30 | ✅ | ❌ | 每日 1 次 |
| 1wk / 1mo | resample 自 1d(`aggregate_derived.py:176-177`)| ✅ | ❌ | **仅** sweep 120min(非事件驱动) |

### 1.2 美股(us)

| 周期 | 来源 | 入库 | 进行中态 | 频率 |
|---|---|---|---|---|
| 5m / 15m / 30m | 源头直取 REST SIP(`us/bar_poller.py:23`;`us.py:149 feed=sip`)| ✅ 仅 fresh 根 | ✅ TradeHub+UsBarTicker | ticker 1s;收线 poll 60s + SIP ~20min 延迟 |
| 60m / 4h | 聚合自 5m(`us/bar_poller.py:69`)| ✅ | ✅ TradeHub | ticker 1s |
| 1d | 源头直取 REST SIP,cd:us:1d ET16:30(+20:30 兜底)| ✅ | ❌ | 每日 |
| 1wk / 1mo | resample 自 1d | ✅ | ❌ | sweep 120min |

注:美股桶滚动 provisional final=true **仅发 bus 不入库**(填 SIP ~20min 延迟洞);权威收线由 SIP poller 落库。美股 60m/4h 的 REST SIP 直取能力(adapter 支持 freq=60)未被任何采集路径调用 = 死代码味道。

### 1.3 加密(crypto)

WS 一连推全部 8 周期(`ws_consumer.py:34`),原生 final + 进行中态。

| 周期 | 来源 | 入库 | 进行中态 | 频率 |
|---|---|---|---|---|
| 5m–1mo(全 8 个)| Binance WS 原生 kline + 启动 backfill | ✅ 仅 `k.x=true` | ✅ WS `k.x=false`(不入库,只写 `:current`)| WS 原生 ~1-2s |

注:crypto bar.ts=**openTime**(`ws_consumer.py:79-82`),其余市场 ts=closeTime(雷区 3)——跨市场消费 bus 的下游若按统一 ts 语义会错位 1 bar。

---

## 2. 实时更新能力 + 频率

| 市场 | 能实时跳的未收线周期 | 推送方 | 频率 |
|---|---|---|---|
| crypto | **全 8 周期(5m–1mo)** | ws_consumer WS | ~1-2s(WS 原生)|
| 美股 | 5m/15m/30m/60m/4h | TradeHub(IEX trades)| **1s**(`FLUSH_INTERVAL_S`)|
| A股 | 5m/15m/30m/60m/4h | quote_bar_ticker(quote)| **10s**(`TICK_INTERVAL_S`)|

- **5 分钟级以上的 intraday 周期三市场都能展示未收线 K 线并实时跳**。
- **不对等**:1d/1wk/1mo 在 A股/美股**无进行中态**(只在收盘 cron 后跳一次),crypto 有(WS 原生)。
- A 股 10s 受限于 sina HTTP 轮询粒度(非 WS);美股 1s 靠 IEX trades 逐笔;crypto 秒级靠 Binance WS。
- ⚠️ "能跳"是能力;**实际是否在跳取决于 §5 的订阅/采集链路**。

---

## 3. DB 盘点 + 可信度 + 清理

### 3.1 库现状(实测)

| 库 | 大小 | 内容 | 可信度 |
|---|---|---|---|
| bars_ashare.duckdb | 4.8M | 日线回 2019,intraday ~2月 | ✅ 可信 |
| bars_crypto.duckdb | 436M | 5 标的回 2017 上市首日,全 8 周期 | ✅ 可信(最大库,正常)|
| bars_us.duckdb | 12M | 日线回 2020;**含 2431 行 1m 孤儿** | ⚠️ 见 §3.3 |
| bars_hk.duckdb | 268K | 表存在但 **0 行**(无 HK collector)| 空占位 |
| intraday_ashare / intraday_us | 各 268K | `intraday_lines`,purge cron 已注册,90 天留存有效 | ✅ |
| state.db(SQLite)| 1.7M | watchlist/signals/fund_flow/symbol_directory 等活跃;5 个空表 | ✅ 活跃 |

### 3.2 可删清单(零代码引用,已被 per-market 取代)

| 文件 | 空间 | 理由 |
|---|---|---|
| `data/bars.duckdb` | 72M | per-market 拆分前遗留单体库;仅 migrate 脚本只读引用 + tests 用 tmp_path;`apps/`+`core/` 零引用 |
| `data/bars.duckdb.before-split-2026-05-29` | 72M | 手工备份(=bars.duckdb)|
| `data/bars.duckdb.before-split-adj-2026-05-21` | 23M | split 复权回算前快照 |
| `data/bars.duckdb.before-sip-2026-05-21` | 21M | SIP 量回算前快照 |
| **合计** | **~187M** | 删前可选再跑一次 per-market 行数核对 |

state.db 的 5 个空表(app_state / fund_flow_sector / health_log / sector_constituents / sectors)标"未使用",**不建议删表**(可能预留 schema);bars_hk 空库保留无害。

### 3.3 🐛 美股 1m 孤儿 + 泄漏(B4)

- 设计要求 1m 不入库(`flush_quotes_to_duckdb` 空壳、`_get_one_minute_bars` 不落库、trade_hub/bar_ticker 注明)。但 `bars_us.duckdb` 有 **2431 行 1m**(26 标的)。
- **根因**:改造前的旧 `ws_consumer`(`bars` 频道,1m 三写)。本次会话 Task 8 已把它换成 `trades`(`04c7669`,不再写 1m),**泄漏源已堵**。
- **✅ 已修(2026-06-01)**:① `insert_bars` 加 `interval != '1m'` 守卫(`a30bc92`,永久杜绝任何路径再写 1m,带 TDD);② 清存量 `DELETE FROM bars WHERE interval='1m'` + VACUUM,**2431 → 0 行**;③ 验证最新 1m ts = `12:27 UTC`(= Task 8 重启时刻),确认泄漏源彻底堵死,此后零新增。

### 3.4 存储边界策略(已确认)

**统一策略(zhonghuai 2026-06-01 确认)**:**1 分钟 + 实时(进行中态)数据不存;5 分钟(含)以上的收线 bar 要存。**

三市场现状核对——该策略即项目设计意图,三市场基本已符合:

| 市场 | 1m | 进行中态 final=false | 5m+ 收线 | 符合? |
|---|---|---|---|---|
| crypto | 不采不存(WS 周期不含 1m)| 不存(只写 `:current`)| 存(`k.x=true`)| ✅ 完全符合,**不需改动** |
| A股 | 已废弃不存 | 不存(ticker 只写 `:current`)| 存 | ✅ |
| 美股 | ✅ **已清零**(B4 已修)| 不存 | 存 | ✅ |

~~唯一偏差 = 美股 1m 孤儿~~ → **B4 已修(`a30bc92`)**:`insert_bars` 加 `interval!='1m'` 守卫 + 清存量 2431→0。三市场现已完全符合存储边界策略。

---

## 4. 前端主动拉取 + SSE 长连接

### 4.1 SSE 长连接(每页)

| 页面 | SSE 连接数 | 端点 |
|---|---|---|
| 个股详情 | **2** | `/sse/bars/{symbol}/{interval}`(K线)+ `/sse/intraday/{symbol}`(分时,仅分时视图)|
| 首页 | **2** | `/sse/bars/batch`(批量价)+ `/sse/bars/{...}`(图表)|

`useKlineStream`(`page.tsx:73` enabled 恒真)始终连 K 线 SSE,与视图模式无关。

### 4.2 主动轮询(SWR)

SSE 只覆盖 **K 线 + 分时**(价格序列);其余面板**无 push 通道**,均轮询拉快照:

| 数据 | 频率 | 后端来源(无 bus,cron 写快照)|
|---|---|---|
| CD 信号 / markers | 30-60s | SQLite(cd:* cron)|
| 量能指标 | 60s(1d 不轮)| 从 bars 算 |
| 资金流 | 开盘 60s | SQLite(fund_flow cron)|
| 筹码 | 一次 | DuckDB(日终预热)|
| quote 报价 | 15s | `cache:quote`(tick_snapshot 10s)|
| profile | 一次 | symbol_directory |
| 首页指数卡/自选/信号 | 60s | bars/history + signals |

轮询保留的原因:这些是**低频快照型数据**,无对应 bus channel,30-60s 拉一次足够,无需为每种开持久连接(部分也是 SSE 之前的历史沿用)。**轮询命中 Redis cache 时几乎零成本,对扩展友好。**

小冗余:首页价格既走 batch SSE 又轮询 `bars/history?limit=2`(拿昨收算涨跌幅),同价格两路。

---

## 5. SSE 释放 + 收线写入是否独立于"有人看"

### 5.1 SSE 生命周期:健全 ✅

- **前端**:`use_kline_stream.ts:110` / `use_intraday_line.ts:123` cleanup `es.close()`;切 interval/symbol/卸载都正确关旧连接,deps 数组正确,**无连接堆积泄漏**。无重连定时器(靠浏览器原生重连)。
- **后端**:`sse_bars._stream_gen` / `sse_intraday._gen` 客户端断开抛 `CancelledError`,双层捕获 `return`,**无协程泄漏、不死循环**。
- 订阅登记 key 断开后 ≤120s TTL 自动过期(软状态)。

### 5.2 🔴 收线采集是否依赖订阅 —— 核心数据完整性

| 市场 | 无人看时收线是否仍采 | 兜底机制 | 风险 |
|---|---|---|---|
| crypto | ✅ 完全独立 | 固定 5 标的 × 8 周期 WS 常驻 | 无 |
| A股 | ✅ 大盘 + watchlist | `bar_poller._DEFAULT_SYMBOLS`(8 指数 5m/15m/30m)**baseline 常驻**(`bar_poller.py:183`)+ signal cron 采 watchlist | 低 |
| **美股** | **❌ 部分停采** | UsBarPoller **只扫订阅、无 baseline**(`us/bar_poller.py:73`);无人看时仅靠 signal cron(15m+)+ fetch:us:5m cron,**且只覆盖 DB watchlist** | **中高(B1)** |

**🐛 B1 + B2 实例化(实测当前数据)**:
- 后端采集集合 = `watchlist.dynamic_universe()` = `repo.all_active_symbols()` = **DB watchlist 显式标的**。当前 DB watchlist 美股 = `[AAPL, BABA, BIDU, GLD, MSFT, NVDA, PDD, QQQ]`。
- 前端首页**硬编码**默认列表(`page.tsx:52`)= `[AAPL, NVDA, MSFT, TSLA, AMZN, META, AMD]`,**不读 DB watchlist**。
- 对照:**TSLA / AMZN / META / AMD** 在首页显示但**不在 DB watchlist** → cron 永不采它们;只在"有人开着页面 → 订阅 → poller 临时采 5m/15m/30m"时才有,**1d 即便开着页面也采不到**(日线只走 watchlist 的 cd:us:1d)。
- **根因**:美股无 baseline 兜底(A 股有)+ 前端"显示哪些"(硬编码)与后端"采集哪些"(DB watchlist)是两套互不同步的名单。

---

## 6. 启动 + kill 后回填健壮性

### 6.1 冷启动(首次部署,DB 空)

| 市场 | 冷启动 backfill | 结论 |
|---|---|---|
| crypto | ✅ 启动 `asyncio.create_task(run_backfill)`(`crypto/main.py:65`),拉到上市首日(`BINANCE_GENESIS`)+ 每日 04:00 兜底 | **健全样板** |
| A股 / 美股 | ❌ **无任何启动历史回填** | 全新部署当下不拉历史,要等下个交易日 cron 慢慢拉(非交易时段部署 → 数据等到下个交易日)|

`data/{market}_backfill_symbols.txt`:仅给 sweep_derived 提供聚合标的清单;**当前磁盘上不存在**→ 优雅降级到 8 个默认标的 → watchlist 非默认标的的派生周期不被 sweep 主动覆盖(B7)。

### 6.2 kill 后缺口回填

**核心事实:除 crypto 外,无任何"启动时检测 DB 末点 vs now 缺口并回补"逻辑。** `refill_consumer` 是纯读路径按需补(cache miss 触发),非启动补。

| 市场 | 当天盘中断档 | 隔夜 | 跨多天 |
|---|---|---|---|
| crypto | ✅ WS 重连 + 04:00 gap 检测 | ✅(<7天)| ❌ **gap 检测只看 7 天**,超 7 天中间断档检测不到(B6)|
| A股 | ✅ sina 自然返回最近 ~5 交易日,幂等补 | ✅ | ❌ 超 5 交易日 intraday 永久空洞 |
| 美股 | ✅ Alpaca 60 天窗,最健壮 | ✅(≤60天)| ❌ 超 60 天空洞 |

**派生周期"中段缺口"不重聚合(B3)**:`_decide_window`(`aggregate_derived.py:119-142`)全量只在头部缺(`source_first < target_first - full_gap`)或尾部新触发;base 周期在**历史中段**被补齐后(first/last 不变),60m/4h/1wk/1mo 对应中段桶**永不重聚合**。

---

## 7. Bug / 风险汇总(按严重度 + 状态)

| # | 问题 | 严重度 | 状态 | 涉及 |
|---|---|---|---|---|
| B1 | 美股收线采集依赖 DB watchlist,无 baseline | 🔴高 | ✅ 已修(CORE_SYMBOLS + UsBarPoller baseline + cron 并入 CORE)| us/bar_poller、signal_jobs |
| B2 | 前端默认列表(硬编码)与后端采集集合(DB watchlist)脱节 | 🔴高 | ✅ 已修(后端 CORE 覆盖前端默认, 决策 a 纯后端对齐)| core_symbols、signal_jobs |
| B3 | 派生周期"中段缺口"不重聚合 | 🔴高 | 🟡 部分缓解(reconcile 全量重聚合;sweep 仍有盲区)| aggregate_derived._decide_window |
| B-startup | A股/美股无冷启动 backfill + 无 kill 后启动 reconcile | 🔴高 | ✅ 已修(startup_reconcile gap检测, 美股实测 filled=14 冷启/0 warm)| startup_reconcile + 两 main |
| B-sina | **sina 数据源不稳定/封 IP**(`stock_zh_a_minute` 返回 banned/空 → IndexError → 熔断反复开;非本次引入,环境/雷区1)| 🔴高 | ⏸ 新发现,待修(超 P0)| sina 通道,需代理池 / em·ths 兜底 |
| B4 | 美股 1m 孤儿 2431 行(泄漏源已堵)| 🟡中 | ✅ 已修 `a30bc92`(守卫+清存量 2431→0)| bars_us、insert_bars |
| B5 | 15m/30m 双源覆盖竞态(实为三市场,经 sweep)| 🟡中 | ✅ 已修 `7115f99`(事件驱动+sweep 两处去 15m/30m 聚合,改直取单源)| ashare bar_poller + aggregate_derived |
| B6 | crypto gap 检测只看 7 天 | 🟡中 | ⏸ 记录待后修(zhonghuai 决定)| crypto/backfill.py:62 |
| B7 | `*_backfill_symbols.txt` 缺失 → sweep 只聚合默认标的 | 🟡中 | 待修 | main.py symbols 来源 |
| B8 | 1d/1wk/1mo 在 A股/美股无进行中态(与 crypto 不对等)| 🟢低 | 设计取舍 | — |
| B9 | 遗留库 bars.duckdb + 备份 ~187M | 🟢低 | ✅ 已删(释放 ~179M,现役库完好)| data/ |
| B-fixed-1 | 美股冬令时盘后桶错位 | — | ✅ 已修 `5c0b77c` | bucket_state.current_bucket |
| B-fixed-2 | `state:subscribe` 无生产者(A股美股实时空转)| — | ✅ 已修 `2ca9ef5` | sse_bars |
| B-fixed-3 | 美股 trades 149 符号撞 405 上限 | — | ✅ 已修 `a76a0ac` | us/ws_consumer |

---

## 8. 修复方案(按优先级,文件级可操作)

> **P0 落地状态(2026-06-02)**:B1/B2/B7/B-startup 已实施(`core/domain/core_symbols.py` + signal_jobs 并入 CORE + UsBarPoller baseline + `startup_reconcile.py` gap检测 + 两 main 接线 + sweep 改 CORE∪watchlist)。**美股侧实测通过**:reconcile 冷启动 filled=14/14、warm restart filled=0(gap检测正确跳过零 burst)。**A股侧逻辑同样就绪,但当前被 sina 环境性封 IP(B-sina)阻塞**——sina 反复返回 banned 响应使熔断器开,live poller 的 `stock_zh_a_minute` 全失败(与 reconcile 无关,reconcile 用 daily 且熔断开时快速失败不加载)。sina 恢复后 A股自动跑通。**B-sina 是独立的数据源稳定性问题(雷区 1),超出 P0,建议另立:接 em/ths 兜底或代理池。**

### P0 — B1/B2/B7/B-startup(数据完整性,互相关联,一并解决)

**核心思路:统一"采集集合"事实源 + 给 A股/美股加启动 reconcile。**

1. **统一采集名单**:把前端 `DEFAULT_WATCHLIST`(`page.tsx:50`)与后端采集集合对齐到**单一事实源**。方案二选一:
   - (a) 后端加"核心标的常驻 baseline"(对等 A 股 8 指数):美股 `UsBarPoller` 增加固定核心名单(如首页默认 7 只 + SPY/QQQ/DIA),无条件采,不依赖订阅/watchlist。
   - (b) 前端默认列表改为读 DB watchlist;同时启动时把核心默认标的 seed 进 DB watchlist(`bootstrap_default` 填默认而非空)。
   - 推荐 (a)+(b) 结合:核心 baseline 常驻采 + 前端读同一来源。
2. **启动 reconcile job**(解决 B-startup/B7):仿 crypto,在 `ashare/main.py` 与 `us/main.py` lifespan 加 `run_startup_reconcile(bar_repo, kline, symbols)`:
   - 用 `bar_repo.fetch_last_ts_map`(已存在)取各 (symbol,interval) DB 末点,与 now 比 gap;超阈值调 `kline.fetch_fresh_bars(start=last_ts, end=now)` 回补。
   - 顺序:先补 1d → 再显式 `aggregate_derived_for_symbol` 全量生成 1wk/1mo(解决冷启动 1wk/1mo 空)。
   - symbols 用 baseline + watchlist `dynamic_universe()`,非订阅集。
   - sweep_derived 的 symbols 也改用 `dynamic_universe()` 而非缺失的 txt 文件(解决 B7)。

### P1 — B3(派生中段缺口)

`_decide_window` 增加"覆盖密度"判断:传入 source/target 的 bar 计数,若 `target_count << source_count / ratio` 超阈值则返回 None(全量重聚合);或 reconcile 补完 base 中段后,显式按回补时间范围调 `aggregate_derived_for_symbol(window_*=<gap_days>)` 覆盖中段。

### P2 — B4 / B5 / B6 / B9

- **B4** ✅ 已修(`a30bc92`):`insert_bars` 加 `interval != '1m'` 守卫;清 `bars_us` 1m 存量 2431→0 + VACUUM。
- **B5** ✅ 已修(`7115f99`):15m/30m 改单一来源(源头直取)。两处去掉聚合:① ashare bar_poller 事件驱动 `targets=("60m","4h")`;② `sweep_derived` 设 `w_15m=w_30m=_NOOP`(三市场共用,US 同样受益)。已核实无覆盖损失(直取 5m 的 poller/cron 同时直取 15m/30m)。
- **B6** ⏸ 记录待后修:crypto `backfill.py:62` gap 窗口只看 7 天。**注意(2026-06-01 复盘):仅扩窗口不够**——现算法只补"最近一个缺口、从缺口一路拉到 now",需配套"检测全部缺口 + 逐段精确回补 + 窗口按 interval 自适应";要"理论零空洞"则需"按时间网格 diff 出缺失再补"。两档方案,zhonghuai 后续决定。
- **B9** ✅ 已删(释放 ~179M):`bars.duckdb` + 3 个 `.before-*` 备份;现役 bars_*/intraday_*/state 完好。

### P3 — B8(可选)

若要 A股/美股 1d 进行中态对齐 crypto,需在 ticker 加日级桶处理(当前 `_INTERVAL_MIN` 不含日级)。优先级低。

---

## 9. 待确认 / 后续

- ~~crypto 存储边界~~ → **已确认**(§3.4):1m+进行中态不存、5m+ 收线存;三市场已符合,唯一偏差是美股 1m 孤儿(B4)。
- **待修范围授权**:P0 涉及 A 股已交付的启动/聚合逻辑,改前需对齐。
- **另立文档**:**多用户 / VPS(~100 并发)扩展性评审**——当前 SSE 全局流 per-consumer 扇出(O(用户×消息))、单 uvicorn worker、Alpaca 免费 IEX 30 符号上限、refill 放大、DuckDB 读耦合 collector,均为"单机单人"假设的产物,100 人会撞墙,需专门的读/实时下发侧多用户化设计。
