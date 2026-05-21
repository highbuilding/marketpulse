# CLAUDE.md

> 给新接手 agent(或 6 个月后的自己)的项目入门。**5 分钟读完,1 个工作日上手。**

---

## 项目定位

**MarketPulse** — 本地运行的多市场行情监控 + 策略指标平台。覆盖 A 股 / 港股 / 美股 / Crypto。**决策支持工具,不做执行**。

- **用户**:zhonghuai(中国境内,A 股口径,**默认中文沟通**)
- **运行方式**:`make dev` 一键启动,后端 FastAPI(8787)+ 前端 Next.js(3000),单机
- **数据底盘**:DuckDB(K 线列存)+ SQLite(state/signals/watchlist,已开 WAL)
- **当前进度**:Plan 1 + Plan 2 + Plan 2.5(CD 信号扩展)已交付。`docs/superpowers/` 是设计文档源头,`docs/TODO.md` 是未实施清单

---

## 设计原则(spec 第 0 章提炼,所有决策都源自这五条)

1. **开源 + 免费优先** — akshare / yfinance / Binance WS,不上付费 API
2. **优雅降级,不 Fail-Fast** — 缺任何源都能跑,UI 诚实标灰对应 tab
3. **国内可用** — A/HK 优先 sina 通道,避开东财直连超时
4. **决策支持非执行** — 不做交易、不做模拟持仓、不做用户系统
5. **单一可跑** — V1 不引入 Prometheus / Grafana / 多实例 / 告警

> 当你在做技术选型或新增依赖时,**先对照这五条**。免费层够用就不要引入付费;能优雅降级就不要 fail-fast;能 SQLite 就不要 Postgres。

---

## 必读雷区(踩过的坑,真实代价说明)

### 雷区 1:py_mini_racer 0.6.0 V8 析构 race(代价:整个 worker SIGABRT)

**症状**:`/tmp/api.log` 末尾出现 `[FATAL:address_pool_manager.cc(67)] Check failed: !pool->IsInitialized().` + `libmini_racer.dylib` 栈。worker 死,端口请求全 `ECONNRESET`。**不是网络问题**。

**根因**:py_mini_racer 0.6.0(PyPI 最新版,**项目已停更**)在 macOS arm64 上 V8 实例析构有 race,**即使顺序调用也概率性 abort**。akshare 大量 sina 系接口内部用它(`stock_zh_a_minute` / `stock_zh_a_spot` / `stock_sector_*` / `fund_etf_*sina` / `stock_zh_index_*`)。

**强制约束**:**所有 akshare 调用经 `core/integrations/akshare.py::ak_call(name, *args, caller, **kwargs)`**。不允许任何业务文件 `import akshare`。

```python
# ✅ 正确
from core.integrations.akshare import ak_call
df = await ak_call(
    "stock_zh_a_minute",
    symbol=sina_code, period=freq, adjust="qfq",
    caller=f"ashare.fetch_intraday:{symbol}:{freq}m",
)

# ❌ 禁止 — 即使加了锁也是埋雷
import akshare as ak
async with mini_racer_lock:
    df = await asyncio.to_thread(ak.stock_zh_a_minute, ...)
```

**验证收口完整性**:
```bash
grep -rn --include="*.py" "^import akshare\|^from akshare" core apps tests
# 期望:仅命中 core/integrations/akshare.py
```

**排查命令**:`grep "racer\." /tmp/api.log | tail -20` 看最后一条 `caller=ak:xxx` 是谁触发。

**根治方案**(未做,在 `docs/TODO.md` 高代价区):用 ProcessPoolExecutor 把 ak 隔离到子进程,子进程崩主进程不受影响。

### 雷区 2:uvicorn `--reload` 不安全(代价:每次 reload 都可能崩)

reload 时 V8 状态污染会触发 SIGABRT。`Makefile dev` 已去掉 `--reload`。**代码变更手动重启**:

```bash
pkill -9 -f "uvicorn apps.api.main:app"; sleep 2
cd /Users/xiangrong/stock/marketpulse
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 6 && tail -5 /tmp/api.log  # 看 "Application startup complete"
```

注意 `>>` 是 append 模式(早期用 `>` 覆盖,崩溃日志会丢失)。**事实源是 `data/logs/api.log`**(见雷区 6),`/tmp/api.log` 仅作 stdout 镜像。

调试时**自己重启**,不要让用户手动操作(用户已明确说过)。

**强制工作流(防"我以为它崩了"误判)**:
干一段活前先 `pkill -9`,干完活后**统一重启一次**。中途不要让 API 服务停在挂掉状态去问用户问题或处理别的事 — 否则用户在浏览器看到"加载失败"会以为是新 bug,实际只是没重启。模板:

```bash
# 开干前:停服务(避免运行中的进程被 commit / git checkout 影响)
pkill -9 -f "uvicorn apps.api.main:app"; sleep 2

# ... 干活、改代码、commit、跑测试 ...

# 收尾:必须重启,不要留 8787 空
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 6 && curl -s -m 3 http://localhost:8787/api/health | grep -o '"status":"[^"]*"'
```

**反模式**(已踩过,2026-05-20):e2e 测试 plan 末尾 `pkill -9` 收尾但忘了重启,16 分钟后用户报"加载失败",实际仅服务没起来。任何 task plan 里 `pkill` 一定要配套 nohup 重启。

### 雷区 3:1d bar 的时间戳约定(踩坑历史:UI 一度显示早一天)

**当前约定**:adapter (`core/adapters/ashare.py:180`) 把 1d bar 的 `ts` normalize 为 **BJT 自然交易日 00:00**(= UTC `(D-1) 16:00`)。所以 ts=`2026-05-17T16:00Z` 表示 **交易日 5/18**(BJT 转换为 `5/18 00:00`)。`bjtDateKey(ts)` 直接得到正确交易日,**不需要任何偏移**。

**历史坑**:早期 `signal_time.ts::effectiveTsIso` 错误地假设 ts 是 sina 原始 "收盘日 16:00 UTC = BJT 次日 00:00",在前端做了 `-8h` 还原,反而把 5/18 显示成了 5/17。2026-05-18 已修复为直通(`effectiveTsIso` 改成 noop)。

**给未来 agent**:`bar_ts` 直接喂 `bjtDateKey` 或 `toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'})` 即可,不要再加任何 +/-8h 偏移。如果 adapter 切源后 ts 语义变了,改 adapter,**别在前端补偏移**。

### 雷区 4:directory bootstrap 跳过逻辑

`apps/api/main.py` 启动期 `dir_svc.refresh_ashare()` 调 `stock_zh_a_spot`,该接口跑过后**污染 V8 状态**(进程后续任何 mini_racer 调用都 abort)。所以现在:**directory 表 < 100 行才刷新**,否则跳过。

**副作用**:7042 条 symbol 列表停在首启动时间,新上市/改名股票永远查不到。属于 workaround,子进程隔离做完才能撤。

### 雷区 5:FastAPI 路径参数顺序

```python
# ❌ 错:/profiles 会被 /{symbol}/profile 的 symbol 吃掉
@router.get("/{symbol}/profile")
@router.get("/profiles")

# ✅ 对:具体路径在前
@router.get("/profiles")        # 先注册
@router.get("/{symbol}/profile")
```

`apps/api/routes/symbols.py` 里就有这个 trap,踩过。

### 雷区 6:日志持久化与崩溃排查(2026-05-20 加固)

**事实源**:`data/logs/api.log`(全量,rotation)、`data/logs/api-errors.log`(WARNING+)、`data/logs/fault.log`(SIGABRT/SIGSEGV C-level 线程栈)。**`/tmp/api.log` 仅作 stdout 镜像,重启 append 不丢**,但不要当事实源。

`core/integrations/logging_setup.py::setup_logging()` 在 `apps/api/main.py` 启动早期(`load_dotenv()` 之后)调一次,无需重复。`faulthandler` 用 fd 直写 fault.log,雷区 1 触发 SIGABRT 时 stdout buffer 来不及 flush 但 fault.log 仍能落盘。

**排查崩溃**:
```bash
tail -100 data/logs/api-errors.log   # 看最后 warning/error
tail -50 data/logs/fault.log          # 看 C 层崩溃栈(如有)
```

---

## 代码规范(项目特有,通用规范不重复)

### 规范 1:单一事实源(SSoT)收口表

**新增任何概念前先看这张表,在 SSoT 内修改,不要散点写。**

| 概念 | SSoT 位置 | 散点示例(已收口) |
|---|---|---|
| akshare 调用 | `core/integrations/akshare.py::ak_call` | adapter / service / route 5 处入口 |
| Interval 元数据(lookback / bars_per_day / 是否信号 / crypto-only) | `core/domain/intervals.py::INTERVAL_CONFIG` | 后端 4 处 + 前端 3 处散点 |
| 前端 Interval tab 配置 | `apps/web/lib/intervals.ts` | K 线 tab / 信号 tab / detail 详情页 tab |
| 信号时间格式化 | `apps/web/lib/signal_time.ts` | 1d -8h 偏移、BJT 自然日切分 |
| 信号表格 UI | `apps/web/components/SignalsTable.tsx` | 详情页 + 关注页公用 |
| Mini-racer 全局锁 | `core/services/_locks.py::acquire` | **仅 ak_call 内部用,业务勿直接用** |
| Symbol market 推断 | `core/domain/markets.py::infer_market` | route / scheduler / kline_service 4 处入口;前端镜像 `apps/web/lib/markets.ts::inferMarket` |
| 优化清单 | `docs/TODO.md` | 跨会话单一来源,完成项划掉但不删 |

**反模式**:发现两个文件出现相似的 interval 列表 / 时间格式化逻辑 / 表格 JSX,立即抽到 SSoT。

### 规范 2:Adapter Protocol 边界

```python
# core/adapters/base.py
class MarketAdapter(Protocol):
    market: str
    async def fetch_snapshot(self, symbols) -> list[Quote]: ...
    async def fetch_history(self, symbol, start, end) -> list[Bar]: ...
    async def health(self) -> HealthStatus: ...
```

切源只改 adapter,**业务层不感知数据来源**。Service / Route / Job 永远通过 Adapter 调外部,不直接 `import akshare` / `import yfinance`(akshare 走 `ak_call`,yfinance 在 adapter 里包装)。

### 规范 3:服务分层

```
Route (薄,只做参数校验和 DTO)
  → Service (业务逻辑、组合多个 repo/adapter)
    → Repo (纯 DB 读写,返回 domain model)
    → Adapter (纯外部 API,返回 domain model)
```

**禁止**:Route 直接调 Repo 或 Adapter。Service 是必经层(即使只是一层 pass-through)。

例外:`/api/health`、纯查 / 纯只读列表的接口可以直接 Repo,但要在 router 文件顶部 docstring 标明。

### 规范 4:DB 引擎边界

- **DuckDB(`data/bars.duckdb`)**:历史 K 线时间序列。列存压缩,大量读、追加写
- **SQLite(`data/state.db`,WAL)**:关系数据 — watchlist / signals / sectors / fund_flow / symbol_directory

**禁止**跨引擎 join,在 Python 层做。如果发现需要频繁跨引擎 join,先思考是不是设计本身有问题。

### 规范 5:错误处理基调

参考 spec §6.1 故障矩阵 — **任何单点故障都不能让整个服务 502**。

```python
# Service 层典型模式
async def scan_many(self, symbols, interval):
    total = 0
    for sym in symbols:
        try:
            total += await self.scan_symbol(sym, interval)
        except Exception as e:  # noqa: BLE001
            log.warning("signal.scan_failed",
                        symbol=sym, interval=interval, error=str(e))
    return total
```

单条失败 → warning 日志 → 继续。**不要让一个 symbol 的失败把整个 batch 拖死**。

### 规范 6:日志结构化

用 structlog,字段统一 `event` + `kv`:

```python
log.info("signal.scan_new", symbol=sym, interval=iv, new=n, total=len(records))
# 不要:log.info(f"scanned {sym} {iv}: {n} new")
```

排查 grep 时 `grep "signal.scan_new" /tmp/api.log` 一行命中。

### 规范 7:测试约定

- 单元测试:`tests/unit/<layer>/test_<file>.py`,**纯函数 / 用 mock**
- 集成测试:`tests/integration/test_*.py`,带 `@pytest.mark.integration` 标记,默认 `make test` 不跑
- E2E:`apps/web/<page>.spec.ts`(Playwright,目前没引入)
- 信号公式回归:用固化数据 fixtures(`tests/unit/indicators/fixtures/600519_daily.csv`)避免依赖网络

---

## superpowers 文档重点提炼

完整设计在 `docs/superpowers/specs/2026-05-13-marketpulse-design.md`(957 行),下面是**只读这一页就能用**的精华。

### 系统总览

```
┌─────────────────────────────────────────────────────┐
│  4 个 MarketAdapter (统一 Protocol)                  │
│  ├─ AShareAdapter  (akshare via ak_call + mootdx)   │
│  ├─ HKAdapter      (sina HTTP + yfinance 备源)       │
│  ├─ USAdapter      (Alpaca + yfinance)              │
│  └─ CryptoAdapter  (Binance WS + CoinGecko)         │
└──────────────┬──────────────────────────────────────┘
               │
        ┌──────┴──────┐
        ▼             ▼
   QuoteCache    BarRepo (DuckDB)
   (TTL 60s)
        │             │
        └──────┬──────┘
               ▼
    Service 层 (KLine / Watchlist / Sector / FundFlow / SignalScan)
               │
        ┌──────┴──────────────────────────────────┐
        ▼                                         ▼
  FastAPI Routes                          APScheduler Jobs
  (/api/markets/*, /api/symbols/*,        (tick 10s, flush 60s,
   /api/cd-signals/*, /api/watchlists/*)   cd:15m/30m/60m/4h/1d cron)
        │
        ▼
  Next.js 14 App Router
  (/market, /symbol/[code], /watchlist, /sector/[name])
```

### 关键数据流

| 路径 | 触发 | 时延 | 路由 |
|------|------|------|------|
| **A. 实时行情** | scheduler 10s | <1s | adapter → QuoteCache → `/api/markets/{m}/overview` |
| **D. 个股 K 线** | 用户访问详情页 | 按需,首次 100ms-5s | KLineService → DuckDB(命中)/ adapter(回填)→ `/api/symbols/{s}/bars` |
| **E. 资金流** | scheduler 分级 cron(北向 1min / 板块 5min / 个股 30min / 收盘后全量) | 分钟级 | adapter → FundFlowRepo → `/api/symbols/{s}/fund_flow` |
| **F. 板块成分** | 每日 09:25 一次 | 日更 | SectorService → SectorRepo |
| **G. CD 信号(Plan 2.5)** | 交易日 cron(BJT 10:30/11:30/14:30/15:00 = 60m;15:10 = 4h;15:30 = 1d;另 15m/30m 每 15/30 min)+ add symbol 异步首扫 | 分钟级 | SignalScanService → SignalRepo → `/api/cd-signals/*` |

### 内存 vs DB 边界(spec §3.4)

| 数据 | 存哪 | 为什么 |
|---|---|---|
| 最新 quote(<60s) | 内存 dict (QuoteCache) | 高频读写,DB 扛不住 |
| 历史 bars | DuckDB | 列存压缩,查询快 |
| 信号 / watchlist / sectors / fund_flow / directory | SQLite + WAL | 小表、关系查询、事务 |

**反模式**:把分钟级 quote 写 SQLite,把基本面 join 写 DuckDB。看到这种代码立刻警觉。

### 冷启动顺序(spec §3.5)

1. lifespan: `state_repo.init()`(开 WAL + 跑 schema)
2. `watchlist.bootstrap_default()`(确保有默认 list)
3. `dir_svc.bootstrap_seeds()`(指数种子)+ 按需 refresh_ashare(<100 行才刷新,见雷区 4)
4. `build_scheduler()` 注册 tick / flush / fundamentals / signal jobs
5. FastAPI 起,前端 `/api/health` 看哪些 adapter ok

**关键**:任何一步失败都不阻塞后续。Alpaca key 没配 → 美股 tab 灰但 A 股照常。

### 路线图速览(spec §8)

| Plan | 范围 | 状态 |
|---|---|---|
| 1 | 骨架 + 4 市场 dashboard | ✅ 已交付(Task 20 Playwright 跳过) |
| 2 | A 股基建:K 线 / 板块 / watchlist / 资金流 / 详情页 | ✅ 已交付 |
| **2.5** | **CD 抄底/卖出信号(超出原计划)** | ✅ 已交付 |
| 3 | 事件管道 + LLM 影响面 | ⏳ 未启动 |
| 4 | 多因子买入候选 | ⏳ 未启动 |

---

## 工作流约定

### 沟通

- **默认中文**(compaction 前后都是)。代码注释、log event 字符串保持中文(项目惯例,看 `git log`)
- **简洁汇报**:做了什么 + 关键验证结果 + 下一步建议。不要长篇总结
- **简单问题直接回答**,不开 plan、不建 task

### 开始非平凡任务的标准流程

1. 用 `EnterPlanMode` + `AskUserQuestion` 对齐(避免大改后被否)
2. 用 `TaskCreate` 跟踪每个子任务,开始时 `in_progress`,做完 `completed`
3. 改完跑验证(下一节)
4. 简短汇报

### 改完的验证三步

```bash
# 1. 后端 import 测试(最快发现循环依赖/拼错)
. .venv/bin/activate && python -c "from apps.api.main import app"

# 2. 前端类型检查(必跑)
cd apps/web && npx tsc --noEmit

# 3. 重启 + 业务冒烟
pkill -9 -f "uvicorn apps.api.main:app"; sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown && sleep 6
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8787/api/health
grep -c FATAL /tmp/api.log  # 期望 0
```

### Git 提交

- **按主题拆 commit**(chore / feat / fix / docs / refactor)
- commit message **中文**,body 详细列改动点。参考最近 5 个 commit 的风格
- **不要 push**,除非用户明确要求
- **不要修改 git config**(身份、签名)
- 提交前确认 `data/*.db` 等运行时文件不在 staged 区(应被 `.gitignore` 忽略)

### 调试与排查

- **API 异常优先看 `/tmp/api.log`**,Web 看 `/tmp/next-dev.log`
- 有 `FATAL` → mini_racer 崩,**不要花时间排查网络**
- 重启服务**自己来**(用户不动手),用上面的命令模板

### 红线(不要做)

- ❌ 任何业务文件直接 `import akshare`(走 `ak_call`)
- ❌ 给 `uvicorn` 加 `--reload`
- ❌ 在接口默认值里硬编码 interval 列表(用 `SIGNAL_INTERVALS`)
- ❌ 跨 DuckDB / SQLite join
- ❌ 把分钟级 quote 写进 SQLite(用 QuoteCache)
- ❌ 让一个 symbol 的失败把整个 batch 拖死(单条 try/except)
- ❌ 给 dev 改时强行 fail-fast(spec 第 0 章原则)
- ❌ push 到远端 / 修改 git 身份(除非明确授权)

---

## 进一步阅读

按推荐顺序:

1. **`docs/TODO.md`** — 14 项已识别未实施的优化,按 价值×代价 分组(高价值低代价先吃)
2. **`docs/superpowers/specs/2026-05-13-marketpulse-design.md`** — 完整设计(957 行,按需读)
3. **`docs/superpowers/plans/2026-05-13-marketpulse-plan-{1,2}-*.md`** — 顶部完成状态总览,Task 详情可作 ground truth
4. **`docs/third_Indicator/`** — 富途指标参考资料(CD/NX/TT 源 + PDF 教程),`core/indicators/cd.py` 翻译自 `CD.ftindex`
5. **`~/.claude/projects/-Users-xiangrong-stock-marketpulse/memory/`** — 用户偏好 + 关键约束(`MEMORY.md` 是索引,会自动加载)

---

## 当前活跃约束(状态时间 2026-05-18)

- **CD 信号在 2026-05-15 后没有新 1d 信号**:不是 bug,公式特性。底/顶背离本身就是低频事件
- **关注页 4h tab** 仅 watchlist 含 crypto 标的时显示(股票市场 4h ≡ 1d)
- **scheduler 每 10s 读一次 sqlite 拿 watchlist**:浪费但单读 <1ms 可忽略,优化项在 TODO
- **`acknowledged` 字段** 后端建好但 UI 没用:死代码,留待"已读"功能或删
- **美股 4h tab** 在 watchlist + 详情页都显示(prepost 16h ÷ 4 = 4 根/天);港股 4h 仅 detail 页可见但与 1d 等价,无新增信号
- 美股数据源 2026-05-20 切回 Alpaca IEX(免费层, 完整支持 1d + 1m/5m/15m/30m/60m intraday);上午接入的 akshare 路径已删除, 但 `directory.akshare_code` 列保留作 dead column
- 美股 1d / intraday 走 Alpaca IEX 前复权(`adjustment='all'`),split + dividend 都已按当前股本回算。2026-05-21 修复:之前 raw 数据导致 NVDA 2024-06 split 处价格跳水(1208 → 120),K 线 + CD 信号失真。如果 user 报"价格跳变",先检查是否在 split 日;如确实未复权,看 `core/adapters/us.py::_fetch_history_alpaca` 的 `adjustment` 参数
