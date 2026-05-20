# 待办优化清单

> 本文档记录已识别但尚未实施的架构/性能/工程化改进。已实施部分不在此列。
> 维护:每完成一项就划掉(`- [x]`)或挪到 CHANGELOG。

按 **价值 × 代价** 分组。建议从"高价值/低代价"先吃。

---

## 高价值 / 低代价

### CI(持续集成)

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

---

## 中价值 / 中代价

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

---

## 美股 intraday 接入(2026-05-20 spec)

- 数据源调研:stooq.com / pandas-datareader / 等 yfinance ban 解封后接回 / 购入 Alpaca paid
- akshare `BRK.B` 类 class share ticker 格式探索
- yfinance 解封后启用熔断恢复路径(`USAdapter.backup_cb` 已写好,`fail_threshold=2`/`reset_after_s=1800`)
- 美股 directory `_US_SEEDS` 启动期批量预热 akshare_code(如果用户体验慢)
- 美股 intraday 暂未接入,前端只显示 1d/1wk/1mo K 线 + 1d CD 信号

---

## 维护

- 完成一项请把 `- [ ]` 改 `- [x]`,并在 commit message 引用本文件位置
- 新发现的优化点写到对应分组,**带上 `价值/代价`** 标签便于排序
- 已实施的清单参见 git log
