# CLAUDE.md

> 给新接手 agent(或 6 个月后的自己)的项目入门。**先读这页(5 分钟),细节按需翻子文档。**
> 更新: 2026-06-18

---

## 项目定位

**MarketPulse** — 本地运行的多市场行情监控 + 策略研究平台。**决策支持工具,不做执行**(不自动下单、不 AI 生成交易建议;策略输出只作纸面指令进沙盒观察)。

- **用户**:zhonghuai(中国境内,A 股口径,**默认中文沟通**)
- **当前范围**:主力 A 股;**美股采集已停**(不入交易大脑);crypto 保留运行,但第一版大脑只用 A 股。
- **运行**:`make dev`(honcho 拉起 collector + api + web;redis 走 docker-compose)。进程:`ashare collector`(8788)+ `crypto collector`(8790)+ `api`(8787)+ `web`(3000)+ `redis`(6379)。`us collector`(8789)默认停。
- **数据底盘**:Redis(热缓存 + Streams bus + 状态/锁)+ DuckDB(K 线/分时,**按市场分文件**)+ SQLite `state.db`(信号/涨停生态/题材/消息/复盘/候选池/回测/纸面指令)。

---

## 设计原则(所有决策都源自这五条)

1. **开源 + 免费优先** — akshare / Binance WS / Alpaca free,不上付费 API。
2. **优雅降级,不 Fail-Fast** — 缺任何源都能跑,UI 诚实标灰(`meta.stale=true`),缺口写 `data_gaps`。
3. **国内可用** — A 股优先 sina 通道,避开东财直连超时。
4. **决策支持非执行** — 不交易、不模拟持仓、不用户系统;策略只出纸面指令。
5. **单一可跑** — V1 不引入 Prometheus / Grafana / 多实例 / 告警。

> 选型/加依赖先对照这五条:免费够用不付费,能降级不 fail-fast,能 SQLite 不 Postgres。

---

## 架构与分层

完整架构图 / 数据流 / 接口地图见 **[docs/system_architecture_overview.md](docs/system_architecture_overview.md)**;交易大脑/回测闭环见 **[docs/strategy_brain_system.md](docs/strategy_brain_system.md)**。这里只给硬约束。

**数据流分层**:
```
外部源(akshare/sina/东财/Binance)
  → collector 采集(唯一 ak_call / 写 DuckDB / 写 cache / 发 bus)
  → 事实层(CD 信号 / 涨停生态 limit_pool / 题材快照 / 盘中消息 / 每日复盘 / 候选池)
  → 盘面层(个股&板块异动 / 竞价快照 / 30min 结论轮)         ← /market 页
  → 交易大脑(因子矩阵 → 6 策略 → vectorbt 回测 → 沙盒门槛 → 纸面指令)  ← /strategy 页
  → API(只读本地)→ Web
```

**进程职责硬约束**:
- **collector(每市场一进程)**:唯一允许 `ak_call`、写自己市场的 DuckDB、写 Redis cache、发 bus;故障隔离。采集范围**唯一事实源 = `collector_symbols` 采集池**(任何市场都无 CORE/自选概念,那只属前端;见 memory `project_collector_symbols_sole_universe`)。
- **api(8787)**:读路径专属。**绝对禁止** `ak_call`(直接+间接)、写 K 线、**直连 DuckDB**(历史走转发);实时读 Redis cache,miss 返 `stale`。
- **redis**:cache + bus + 状态,不持久化历史。
- 周期任务**优先 worker loop / 事件 / 读时计算,不必要的 APScheduler cron 一律去掉**(见 memory `feedback_avoid_cron_prefer_stream`)。

---

## 红线(绝不做)

- ❌ 业务文件 `import akshare`(走 `ak_call`);**api 经任何路径触发 `ak_call`**。
- ❌ **api 直连 DuckDB**(read_only 也不行,撞锁/踢写;历史走 collector 转发)。
- ❌ 给 `uvicorn` 加 `--reload`(V8 SIGABRT)。
- ❌ 跨 DuckDB/SQLite join;把分钟级 quote 写进 SQLite。
- ❌ 一个 symbol 失败拖垮整个 batch(单条 try/except)。
- ❌ Redis key 散点拼接(经 `core/cache/keys.py`)。
- ❌ 采集进程用 CORE/自选概念(只认 `collector_symbols`)。
- ❌ `pkill` 不配套 nohup 重启。
- ❌ push 远端 / 改 git 身份(除非明确授权);自动下单 / AI 生成交易建议。

`grep -rn "ak_call" apps/api/` 必须只命中注释。

---

## 必读约束速查(详细来龙去脉见 [docs/pitfalls_and_conventions.md](docs/pitfalls_and_conventions.md))

- **ak_call 收口**:所有 akshare 经 `core/integrations/akshare.py::ak_call`(子进程隔离 + breaker/ratelimit/outlet 三层中间件)。
- **bar 时间戳**:1d = BJT 自然日 00:00(前端零偏移);intraday = close 时刻;crypto 例外用 open 对齐。
- **DuckDB 单写多读互斥**:api 读历史一律走 collector `/internal/bars/history` 转发(雷区 6)。
- **FastAPI 路径**:具体路径在 `/{param}` 之前注册。
- **日志事实源**:`data/logs/*`(已 rotate);`/tmp/*.log` 仅 nohup 镜像,重启用 `>` 不用 `>>`。
- **服务重启自己来**,不让用户动手;任何 `pkill` 配套 nohup 重启。

### 单一事实源(SSoT)收口表 — 新增概念前先查这张表

| 概念 | SSoT 位置 |
|---|---|
| akshare 调用 / 三层中间件 | `core/integrations/akshare.py::ak_call` + `{breaker,ratelimit,outlets/}.py` |
| Redis key 命名 | `core/cache/keys.py`(所有 key 必经) |
| 交易日识别 | `core/domain/market_calendar.py::is_trading_day` |
| Symbol market 推断 | `core/domain/markets.py::infer_market` + `apps/web/lib/markets.ts::inferMarket`(前端镜像) |
| A 股盘面时段 | `core/domain/market_sessions.py::ashare_phase` + `apps/web/lib/markets.ts::ashareBoardPhase`(镜像) |
| 采集池(采集范围唯一事实源) | `core/persistence/collector_symbol_repo.py` |
| K 线游标分页(后端) | `duckdb_repo.py::fetch_history_paged` + `apps/collector/base.py::attach_bars_history_route` |
| K 线历史/实时(前端) | `apps/web/lib/use_bars_history.ts` / `use_kline_stream.ts` |
| 涨停生态 | `core/persistence/limit_pool_repo.py`(涨停/炸板/跌停/昨板/连板) |
| 盘面异动/竞价 | `core/services/market_changes_service.py` + `apps/api/routes/market_changes.py` |
| 结论层(盘中/复盘/30min轮) | `core/services/market_conclusion_service.py` + `apps/api/routes/conclusions.py` |
| 候选池 / 低频事实 | `core/services/{watch_candidate,lowfreq_fact}_service.py` |
| 策略回测 / 纸面指令 | `core/services/strategy_backtest_service.py` + `core/persistence/strategy_backtest_repo.py` |
| Leader 状态 | `core/scheduler/leader_gate.py::set_leader/is_leader` |
| 优化清单 | `docs/TODO.md`(跨会话单一事实源) |

> 反模式:发现两处出现相似 interval 列表 / 时间格式化 / 表格 JSX,立即抽 SSoT。

---

## 工作流约定

**沟通**:默认中文(代码注释、log event 也中文);简洁汇报(做了什么 + 关键验证 + 下一步);简单问题直接答,不开 plan。

**非平凡任务**:`EnterPlanMode` + `AskUserQuestion` 对齐 → `TaskCreate` 跟踪 → 改完验证 → 简短汇报。

**改完的验证三步**:
```bash
# 1. 后端 import(最快抓循环依赖/拼错)
. .venv/bin/activate && python -c "from apps.api.main import app; from apps.collector.ashare.main import app as a; from apps.collector.crypto.main import app as c; print('OK')"
# 2. 前端类型
cd apps/web && npx tsc --noEmit
# 3. 单测 + (改了 api/collector 则)重启 + 冒烟
. .venv/bin/activate && pytest -m "not integration" -q
```

**重启模板**(改 api/collector 后,自己来):
```bash
pkill -9 -f "apps.collector.ashare.main"; pkill -9 -f "uvicorn apps.api.main:app"; sleep 2
docker compose -f docker-compose.dev.yml up -d redis
nohup bash -c '. .venv/bin/activate && python -m apps.collector.ashare.main' > /tmp/collector_ashare.log 2>&1 & disown
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 & disown
sleep 9
curl -s -m3 http://localhost:8787/api/health | grep -o '"status":"[^"]*"'
curl -s -m3 http://127.0.0.1:8788/health | grep -o '"status":"[^"]*"'
# 需要美股/crypto 时再起 apps.collector.{us,crypto}.main(分别 8789/8790)
```
> 收尾用 `>`(截断)非 `>>`;`pkill` 必配套重启(反模式:忘重启 → 用户报"加载失败")。

**Git**:按主题拆 commit(feat/fix/docs/refactor/test),message 中文 body 列改动点;**不 push、不改 git 身份**;提交前确认 `data/*.db` 不在 staged。

**前端验证**:优先 Playwright/Chrome 驱动真实浏览器 + 拦网络请求当证据(见 memory `feedback_playwright_evidence_testing`)。

---

## 当前状态(2026-06-18)

- **交易大脑/回测闭环已落地**(A 股):因子矩阵 → 6 策略 → vectorbt 回测 → 沙盒门槛 → 纸面指令,`/strategy` 页展示。收盘后 16:05/18:30 跑回测。详见 strategy_brain_system.md。
- **盘面已重做**:`/market` 改为时段驱动(竞价 → 开盘 → 盘中异动 + 30min 结论轮 → 尾盘),实盘消息列表撤出(数据仍落库供回放)。30min 结论轮**读时切片**,异动采集走常驻 worker(非 cron)。
- **美股采集停止**;crypto 保留;HK 指数未实装。
- **env 标的分层(test30/prod400)已废弃**:采集范围以 `collector_symbols` 为准(memory `project_env_tiering` 已标注过时)。
- 三市场都有实时 K 线进行中态 + 分时图(分时图独立库 `intraday_{market}.duckdb`)。
- CD 信号 1d 偶尔几天无新信号是公式特性(底/顶背离低频),非 bug。

---

## 进一步阅读

1. **[docs/system_architecture_overview.md](docs/system_architecture_overview.md)** — 总体架构 / 数据流 / 接口地图(mermaid,**首选总览**)
2. **[docs/strategy_brain_system.md](docs/strategy_brain_system.md)** — 交易大脑 / 复盘因子 / 回测闭环
3. **[docs/pitfalls_and_conventions.md](docs/pitfalls_and_conventions.md)** — 6 大雷区 + 代码规范详版
4. **`docs/TODO.md`** — 已识别未实施的优化
5. **`docs/superpowers/specs/`** — 历史设计 spec(稳定采集 / 进程拆分 / 原始 spec)
6. **`docs/third_Indicator/`** — 富途指标参考(`core/indicators/cd.py` 翻译自 `CD.ftindex`)
7. **`~/.claude/projects/-Users-xiangrong-stock-marketpulse/memory/`** — 用户偏好(`MEMORY.md` 索引,自动加载)
