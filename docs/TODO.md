# 待办优化清单

> 本文档记录已识别但尚未实施的架构/性能/工程化改进。已实施部分不在此列。
> 维护:每完成一项就划掉(`- [x]`)或挪到 CHANGELOG。

按 **价值 × 代价** 分组。建议从"高价值/低代价"先吃。

---

## 高价值 / 低代价

### A 股盘中实盘消息后续增强(2026-06-15)

> 当前盘中消息规则和架构已收口到 `docs/2026-06-15-ashare-intraday-watch-runbook.md`。下面是下一阶段最值得补的能力。

- [ ] **全 A 宽度或低成本宽度代理**
  - 现状:`LiveMessageService` 的宽度消息基于 `collector_symbols`,口径是采集清单,不是全 A。
  - 价值:区分“采集样本强/弱”和“全市场真实强/弱”,减少指数背离误判。
  - 方向:优先找低频/低成本数据源;如果只能从全 A 快照算,要做严格 QPS 和缓存策略。

- [ ] **题材状态机加入资金流确认**
  - 现状:v3 已从绝对数量改成比例阈值,但仍主要依赖涨跌幅和成分扩散。
  - 价值:过滤纯脉冲行情,提高“题材启动/扩散”的有效性。
  - 方向:把板块资金流、核心股资金流、成交额放大纳入 `ThemeState.evidence` 和消息规则。

- [ ] **涨停/连板/炸板结构识别**
  - 现状:只粗略统计题材成分涨幅,没有可靠涨停状态、连板高度、炸板率。
  - 价值:A 股短线盘中判断的关键变量。
  - 方向:先做采集清单内的涨停/接近涨停/大幅回落代理,后续再扩全市场。

- [ ] **题材间轮动识别**
  - 现状:单题材状态机已可写 `theme_states`,但没有跨题材比较和轮动事件。
  - 价值:识别“主线退潮 / 新主线接力 / 权重切换到题材”等更接近看盘语言的消息。
  - 方向:对 `theme_states` 做 5m 时间窗比较,生成轮动类 live message。

- [ ] **盘后回放视图**
  - 现状:消息和状态已写 SQLite,但没有按时间轴回看。
  - 价值:复盘规则误报/漏报,用于调整 `RULE_VERSION`。
  - 方向:页面按时间线串起 `live_messages`、`theme_snapshots`、指数和宽度状态。

### 新机器重建数据库 — warmup 补足股票/美股全周期全历史(2026-05-30)

> 背景:crypto 已做到"collector 启动自动回填 5 标的 × 8 周期 → 上市首日全历史"(`apps/collector/crypto/backfill.py::run_backfill`,lifespan 内 `create_task` 自动触发,已验证拉到 2017-08-17)。但股票/美股的 `apps/warmup.py` 还停在旧版,**新机器重建数据库时股票/美股历史不完整**,K 线滑动翻页(全市场通用的 `useBarsHistory`)在非 1d 周期会空白。三个缺口:

- [ ] **warmup 扩展到全部周期**(当前只拉 `interval="1d"`)
  - 现状:`apps/warmup.py::warmup` 写死 `svc.get_bars(sym, interval="1d", ...)`,缺 `5m/15m/30m/60m/4h/1wk/1mo`(前端 9 个周期见 `apps/web/lib/intervals.ts`)
  - 改法:循环 `core/domain/intervals.py::INTERVAL_CONFIG` 的所有周期(参考 crypto `backfill.py::INTERVALS` 写法)
  - 底层已就绪:`KLineService.get_bars` 已会落库(`core/services/kline_service.py:58 insert_bars`),核心只是加周期循环
  - **价值:高(新机器重建数据库的前提) / 代价:低**

- [ ] **warmup 放开时间窗口到上市首日**(当前受 `--days` 限制,默认 365)
  - 现状:rolling window,对齐 crypto 的"完整历史"体验应支持拉到尽可能早
  - 改法:参考 crypto `BINANCE_GENESIS` 固定起点思路;akshare(A股)/Alpaca(美股)各有最早可得日期(美股 Alpaca IEX 实测 1d 到 2020、intraday 受限,见本文档美股章节——拉到源能给的最早即可)
  - **价值:中 / 代价:低-中**

- [ ] **(可选)ashare/us collector 启动自动回填**(对齐 crypto,免手动 make warmup)
  - 现状:股票/美股要手动 `make warmup`;crypto 是 lifespan 内自动 + 每日兜底
  - 改法:在 `apps/collector/{ashare,us}/main.py` lifespan 挂首次回填 task(参考 crypto main.py:65-67)
  - **价值:中(新机器开箱即用) / 代价:低**



- [ ] **加 GitHub Actions workflow `.github/workflows/ci.yml`**
  - 后端:`pytest tests/`(目前 tests/unit 下已有用例,无人自动跑)
  - 前端:`cd apps/web && npx tsc --noEmit`
  - 依赖检查:确认 requirements.txt / pyproject.toml 能在干净环境装齐
  - 第一次跑可能会暴露已有 tests 的 fail,先修到全绿
  - **价值**:重构 safety net,下面"1d normalize"等大改才有底
  - **代价**:~30 分钟

### 错误处理 / UI 反馈

- [ ] **前端加 ErrorBoundary + SWR 错误展示**
  - 现状:SWR fetcher 失败只 `throw new Error(\`${r.status}\`)`,UI 没提示用户
  - 至少在 `WatchlistSignalsPanel` / `CDSignalPanel` / `FundFlowPanel` 加 `error` 状态展示
  - 加一个全局 ErrorBoundary 兜底渲染崩溃

### 监控可观测性

- [ ] **`/admin/racer-stats` 接口**
  - 暴露最近 100 次 racer 调用、最大等待时间、失败率
  - 排查 mini_racer 卡顿不再靠 `grep /tmp/api.log`
  - 复用现有 `core/services/_locks.py` 的日志数据(改成 deque buffer 即可)

### 死代码 / 未使用功能

- [ ] **`acknowledged` 字段**:`indicator_signals.acknowledged` + `count_unacknowledged` API + 前端 `ackCDSignal` 全建好了,**但 UI 里没人调** —— 死代码。
  - 方案 A:在事件流"当天"section 加一键已读按钮 + 顶栏未读 badge
  - 方案 B:删掉相关代码

### Plan 1/2/3 进度

- 2026-05-27 ✅ Plan 1 完成:Redis 基建 + collector 进程拆分。
- 2026-05-27 ✅ Plan 2 完成:ak_call 三层中间件(Outlet/Breaker/Ratelimit)+ Leader + index_minute/dashboard/refill_consumer job。
- 2026-05-27 ✅ Plan 3 完成:api 全部读 cache(0 ak_call)+ 前端 stale 染灰 + Plan 1/2 退化全修。spec §0.2 中 4 个症状(K 线分时图慢/大盘慢/重复 K 线慢/分时 500)全部消除。
- 2026-05-28 ✅ Plan 3 后续 hotfix(A/B/C):/top + /chip_summary + /ai/market-packet 走 cache,**apps/api/ 真正 0 ak_call(直接 + 间接)**。15 新单测,339 total passing。
- 2026-05-28 ✅ **交易日历集成**(exchange_calendars):tick/index_minute/market_top/ai_packet 4 个高频 job 接入 `is_trading_day(market)`, A/HK/US 各自识别节假日 + 调休。节假日 ~30% sina + ~50% em 调用节流, 351 total tests passing。
- 2026-05-28 ✅ **大盘 IndexCard 扩展字段**(全市场 market_extras): A 股 8 指数显示北向资金净流入 + 成交额 + 同比基线; 美股新增 SPY/QQQ/DIA ETF 代理(Alpaca + IEX feed)显示 prev_close + amount(亿美元)。修复 prev_close 涨跌幅计算 bug(原用首点跳空缺口算)。spec: docs/superpowers/specs/2026-05-28-market-index-extended-design.md
  - **港股 IndexCard 仍未实装**(原计划 Plan B 候选): `/api/indices/HSI.HK/minute` 等仍返 `stale=true, reason="hk_index_collector_pending"`。 `stock_hk_index_spot_em` 不存在 + akshare 内未发现现成接口, 需要重新调研数据源
  - **Crypto IndexCard 暂搁置**: 老 `apps/collector/jobs/crypto_index_minute.py` 已删除,coingecko 限频严重(429),需要切 Binance Spot API 或换源

### Plan 3 后发现的 service 层 ak_call 间接调用 — ✅ 已全部修复(2026-05-28)

- [x] **`/api/markets/{m}/top`** — 已切到 collector market_top job + cache:market:{m}:top
- [x] **`/api/symbols/{s}/chip_summary`** — 已加 ChipService.get_summary_cache_only,api 走 cache_only
- [x] **`/api/ai/ashare/market-packet`** — 已切到 collector ai_packet job + cache:market:ashare:ai_packet

### Plan 1 引入的已知退化(Plan 2/3 修)— ✅ 已全部修复

- [ ] **QuoteCache 跨进程孤岛**(2026-05-27 引入)
  - 现状:scheduler 拆到 collector 进程后,`QuoteCache` 实例不再跨进程共享。collector 的 tick 写自己进程的内存 cache,api 的 `/api/symbols/{s}/quote` 与 `/ws/ticks` 读自己进程的(永远空)cache。
  - 表现:**api 的 quote 接口静默返回 `price: null`、ticks WebSocket 推空数组**。功能性退化,但用户体验上"加载中"也能看,不会 crash。
  - 修复时机:**Plan 3 Stage 5** 把 quote 读路径切到 Redis cache(collector 写、api 读)即解决。在那之前,前端用 `/api/symbols/{s}/bars?days=1` 拿最新 bar 当快照可作 workaround。
  - 价值:解决会让前端 quote 显示恢复正常
  - 代价:Plan 3 Stage 5 内顺便做,无独立成本

### Plan 2 引入的已知未尽事项(Plan 3 修)— ✅ 已全部修复

- [ ] **Leader 锁未真正门控 cron job**(2026-05-27 引入)
  - 现状:`leader.is_leader()` 已经在 collector 启动期 acquire,但 scheduler 注册的所有 cron job 不检查 leader 状态就执行
  - 影响:单节点部署无害(永远是 leader);多节点部署时会双写
  - 修复:在 `core/scheduler/scheduler.py` 的所有 `attach_*_job` 里包一层 `ensure_leader()` gate(从 leader 单例查 is_leader,非则 return)
  - 代价:小,但要逐个 job 包一遍

- [ ] **`_redis_for_mw` 连接 shutdown 未优雅关闭**(2026-05-27)
  - 现状:`apps/collector/main.py` 在 finally 没有 `await _redis_for_mw.aclose()`,collector 退出前会有 ResourceWarning
  - 影响:仅日志噪音,无功能问题
  - 修复:在 finally 块末尾加 `await _redis_for_mw.aclose()`

- [ ] **refill_consumer 无 DLQ 重试机制**(2026-05-27)
  - 现状:`refill_consumer.consume_loop` 在 `finally` 块 xack 每条消息,即使 refill_fn 失败也会 ack,丢失重试机会
  - 影响:api 发布的 refill 请求一次失败永远失败,前端只能下次访问再触发
  - 修复:把 xack 移到成功路径;失败的消息留在 pending entries list (PEL),用 XCLAIM 重投或定期扫描
  - 优先级:Plan 3 引入 api publisher 之前不紧迫

- [ ] **index_minute 非交易时段无意义刷新**(2026-05-27)
  - 现状:24/7 每 30s 调一次 sina,夜里也跑 ~700 次/晚
  - 影响:浪费 sina 调用配额,可能引来限流
  - 修复:在 `refresh_all_indices` 入口判断 BJT 是否在 09:00-16:00 范围,否则跳过
  - 优先级:低,但 Plan 3 顺手做

---

## 中价值 / 中代价

### 4h bucket 时钟对齐(跨市场)

- [ ] **`_group_resample` 按时区刻度切, 而非数组下标切**
  - 现状:`core/services/kline_service.py::_group_resample` 用 `for i in range(0, len(bars), group_size)` 切。当源数组起点不在 4h 边界(如美股盘中第 1 根是 09:30 ET 而非 08:00 ET 起点),bucket 错位
  - 期望:按市场所在时区的 4h 自然刻度切(美股 ET 04/08/12/16/20, A 股 BJT, HK BJT, crypto UTC)。富途/老虎/TradingView 都按时钟切
  - 影响:K 线显示 + CD 信号 trigger 时刻 — 4 个市场全覆盖
  - 估代价:中(market → 时区映射, 测试要回归 4 个市场)
  - 触发:2026-05-21 美股 SIP 切换后 4h tab 启用,如发现 bucket 错位影响判断, 优先级抬高

### 性能 — N+1 / 重复查询

- [ ] **tick_snapshot_once 内存化 watchlist universe**
  - 现状:`core/scheduler/jobs.py::tick_snapshot_once` 每 10 秒读一次 sqlite watchlist
  - 4 market × 6/分钟 = 1440 次/天纯无效查询
  - 改法:registry 维护内存 `_dynamic_universe`,watchlist add/remove API 主动 invalidate

- [ ] **CDSignalPanel 的"已扫过就跳"**
  - 现状:每次切 tab 都 POST `/scan` 等 3-8 秒,即使 watchlist 已扫过
  - 改法:`app_state` 表记 `scan:{symbol}:{interval}` 时间戳;前端先读它,N 分钟内扫过就跳过

### 架构 — universe 概念统一

- [ ] **registry 统一 universe**
  - 现状:`config/sources.yaml::default_universe` 是静态;watchlist 是动态;两套语义,部分 scheduler 任务只用静态
  - 改法:`registry.universe(market) = static_universe ∪ watchlist_symbols(market)`,一个方法所有 scheduler 都调

### 工程化

- [x] **去掉 Makefile dev target 的 `--reload`** _(2026-05-15)_
  - `--reload` 在我们项目下与 mini_racer 反复触发崩溃(见 [[project-mini-racer-lock]])
  - 已改:加注释说明,代码变更请手动重启

- [ ] **加 pre-commit + ruff**
  - 限制基本格式 / 简单 lint,push 前在本地兜底一道

### 数据完整性

- [ ] **CD 公式 vs 富途/通达信对账测试**
  - 固化几组从富途截图的"已知信号点",加端到端断言"这些 bar_ts 必须有 buy/sell"
  - 防止以后改公式或采样窗口时静默回归

### 类型 / DTO

- [x] **后端 SignalDTO.interval 从 str 改成 Literal** _(2026-05-15)_
  - `apps/api/routes/cd_signals.py::SignalDTO.interval: SignalIntervalT`,Literal 派生自 `SIGNAL_INTERVALS`
  - 顺手在该文件导出 `SignalIntervalT` 别名

- [x] **前端 `Market` 类型收紧** _(2026-05-15)_
  - 核实:`Market = 'ashare' | 'hk' | 'us' | 'crypto'` 实际已覆盖,后端 `_infer_market` 也返回这 4 个
  - 顺手把 `SymbolProfile.market` 和 `SearchHit.market` 从 `string` 改成 `Market`

### 安全

- [ ] **`POST /api/cd-signals/{id}/ack` 加幂等保护**
  - 当前没鉴权也没幂等,重复调用会重复写 audit log(如果加了的话)

---

## 高价值 / 高代价

### 数据规范化

- [ ] **1d bar 在 adapter 层 normalize**
  - 现状:sina 给的 daily bar `ts = 收盘日 16:00 UTC = BJT 次日 00:00`,前端 `signal_time.ts::effectiveTsIso` 在显示层 -8h 打补丁
  - 问题:`KLineChart` 显示日线时也不一致;`signal_repo` 存的是 sina 原始 ts
  - 改法:`AShareAdapter.fetch_history` 返回的 1d bar `ts = 收盘日 00:00 UTC`(把 sina 16:00 减 16h)。一处规范,前端不用各自打补丁。
  - **风险**:破坏性改动,要清掉 duckdb 历史 bar 数据重抓
  - **依赖**:做完 CI 之后再做(test_kline_service 是 safety net)

### 根治 mini_racer 崩溃

- [ ] **ProcessPoolExecutor 隔离 ak 调用**
  - 现状:`core/integrations/akshare.py::ak_call` 用全局 asyncio.Lock 串行,但 py_mini_racer 0.6.0 在 macOS arm64 仍有析构 race,概率性 SIGABRT
  - 改法:`ak_call` 内部走 ProcessPoolExecutor,每次调用在子进程跑;子进程崩了主进程不受影响,主进程 worker 永远不会 SIGABRT
  - **风险**:子进程冷启动慢(每次 ~500ms-1s),要做 worker pool 复用;数据序列化(pandas DataFrame 跨进程 pickle)有开销
  - **代价**:1-2 天

### SQLite 并发写

- [ ] **连接池化(可选,看流量)**
  - 现状:`aiosqlite.connect` 每次开新连接;WAL 已开,SQLITE_BUSY 风险已小
  - 改法:维护一个 `aiosqlite.Connection` 单例,所有 repo 共用
  - 触发条件:出现 SQLITE_BUSY 错误时再做

---

## 已知小 bug

| 位置 | 描述 | 修法 |
|------|------|------|
| ~~`apps/api/routes/cd_signals.py:53,143`~~ | ~~`ScanBody.intervals=None` 时 fallback 写死 `['60m','4h','1d']`,**漏掉 15m/30m**~~ | ✅ 已修 _(2026-05-15)_:改用 `SIGNAL_INTERVALS`,空 body POST 自动扫 5 个周期 |
| `apps/api/main.py:50-58` | 启动期 `directory bootstrap` **只在表 <100 行才刷新**,新上市/改名股票永远查不到/显示旧名 | 子进程隔离 ak(高价值/高代价那项)做完后撤这个 workaround,改为每日定时刷新 |

---

## 美股接入未实施事项(spec 2026-05-18 §9)

- `signal_service.scan_symbol(regular_only=)`:美股盘前盘后噪声过滤选项 + UI "Extended Hours" toggle
- 4h bucket 按时钟对齐(消除 yfinance 偶发缺 bar 时的偏移)
- 富途 SDK 接入(yfinance 失效时的 Plan B)
- 美股 dashboard 板块卡(SPY / QQQ / DIA 主要指数代理)
- 美股资金流(institutional holders / 13F)— 待数据源调研
- HK / Crypto 关注页内容接入(本期骨架,功能开发中)
- K 图 markers 新 bar 自动同步(SWR refreshInterval 在交易时段开)
- IndexCard 时区适配(目前 hardcode BJT,A 股 dashboard 用,迁移到 chart_time 工厂版本)
- HK adapter 增加 `fetch_intraday(freq='5')`(yahoo `0700.HK?interval=5m` 或 sina HK):接入后 60m / 4h 自动走 `aggregate_intraday` 富途口径(`10:30/11:30/12:00/14:00/15:00/16:00`),前端详情页 60m / 4h tab 即可显示。当前 HK 60m / 4h 接口 500,前端 tab 已隐藏,无用户感知影响。**价值:中(关注页 4h 完整化) / 代价:低**

---

## 美股 Alpaca IEX 已接入(2026-05-20 spec 修订)

后端美股数据全部走 Alpaca IEX 主源(免费, 实测 1d 2020-至今, intraday 5m/15m/30m/60m 60 天历史, 1m 7 天)。

后续可选优化:
- **SIP 付费升级**($99/月):获 2016 之前的全市场历史 + 实时 SIP feed(无 15min 延迟)。如需扩历史窗口或精度
- Alpaca historical bars rate limit 监控:目前 200/min 够用;watchlist 增长后需重新评估
- `directory.akshare_code` 列保留作 dead column,日后若再用 akshare 直接复用 schema
- 美股 4h 暂不支持(Alpaca IEX prepost bar 稀疏,4h 重采样残缺);对齐 A 股 / HK 口径

---

## 维护

- 完成一项请把 `- [ ]` 改 `- [x]`,并在 commit message 引用本文件位置
- 新发现的优化点写到对应分组,**带上 `价值/代价`** 标签便于排序
- 已实施的清单参见 git log
