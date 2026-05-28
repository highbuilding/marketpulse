# CLAUDE.md

> 给新接手 agent(或 6 个月后的自己)的项目入门。**5 分钟读完,1 个工作日上手。**

---

## 项目定位

**MarketPulse** — 本地运行的多市场行情监控 + 策略指标平台。覆盖 A 股 / 港股 / 美股 / Crypto。**决策支持工具,不做执行**。

- **用户**:zhonghuai(中国境内,A 股口径,**默认中文沟通**)
- **运行方式**:`make dev` 一键启动,**三进程架构**(collector + api + redis + web),honcho 拉起
- **数据底盘**:Redis(热缓存 + bus)+ DuckDB(K 线列存)+ SQLite(state / signals / watchlist)
- **当前进度**:Plan 1 + 2 + 3 + 后续 hotfix + 交易日历集成都已交付。`docs/superpowers/` 是设计源头,`docs/TODO.md` 是未实施清单

---

## 设计原则(spec 第 0 章,所有决策都源自这五条)

1. **开源 + 免费优先** — akshare / yfinance / Binance WS / Alpaca free,不上付费 API
2. **优雅降级,不 Fail-Fast** — 缺任何源都能跑,UI 诚实标灰(`meta.stale=true`)对应卡片
3. **国内可用** — A 股优先 sina 通道,避开东财直连超时
4. **决策支持非执行** — 不做交易、不做模拟持仓、不做用户系统
5. **单一可跑** — V1 不引入 Prometheus / Grafana / 多实例 / 告警

> 选型或加依赖时**先对照这五条**。免费层够用就不要付费;能降级就不要 fail-fast;能 SQLite 就不要 Postgres。

---

## 架构总览(2026-05-28 完成态)

```
┌─────────────────────────────────────────────────────────┐
│  apps/collector/        ← 所有 ak_call / 写 DB / 写 cache │
│  ├─ APScheduler cron jobs (tick / index_minute / top /   │
│  │   ai_packet / dashboard / cd:* / fund_flow / chip)    │
│  ├─ leader 抢 Redis SETNX 锁,只 leader 跑 cron          │
│  └─ ak_call 经三层中间件: Outlet → Ratelimit → Breaker  │
│         │                                                │
│         ▼ 写                                             │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Redis (hot cache + Streams bus + state + lock)  │   │
│  │  • cache:quote/index/market/bars/chip/fundflow   │   │
│  │  • bus:quote.tick / bars.updated / refill_request│   │
│  │  • state:leader/source/outlet/inflight           │   │
│  │  • ratelimit:source:sina|em|ths                  │   │
│  └──────────────────────────────────────────────────┘   │
│         ▲ 读 (绝不打 ak_call)                            │
│         │                                                │
│  ┌──────────────────────────────────────────────────┐   │
│  │  apps/api/  ← FastAPI 8787 — 读路径专属          │   │
│  │  - 所有路由读 cache,miss 返回 meta.stale=true   │   │
│  │  - DB fallback(Redis 挂时直读 DuckDB/SQLite)   │   │
│  └──────────────────────────────────────────────────┘   │
│         ▲                                                │
│         │ HTTP                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │  apps/web/  Next.js 3000 — StaleBadge 染灰      │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**进程职责硬约束**:
- **collector** 唯一允许 `ak_call`、写 DB、写 Redis cache、发 bus
- **api** **绝对禁止** `ak_call`、写 K 线 bars、抢 mini_racer 锁
- **redis** 不持久化历史(那是 DuckDB 的事),仅作 cache + 总线

`grep -rn "ak_call" apps/api/` **必须只命中注释字符串**。

---

## 必读雷区(踩过的坑)

### 雷区 1:py_mini_racer V8 race(已根治,知道历史就行)

**症状**:`SIGABRT` + `libmini_racer.dylib` 栈 → worker 死,`ECONNRESET`。
**根治措施**(Plan 1+2):
- akshare 调用全走 `ak_call` → `_run_ak_in_child_process` **子进程隔离**,子进程崩主进程不受影响
- 三层中间件 Outlet/Ratelimit/Breaker 包住 ak_call,异常自动熔断
- 全局 `_racer_acquire` 锁仍保留作 watchdog 入口(>60s dump 线程栈到 `fault.log`)

**今天的约束**:
- 不允许任何业务文件 `import akshare`,走 `core/integrations/akshare.py::ak_call`
- **api 路由不允许任何 ak_call**(直接 + 通过 service 间接)。`grep -rn "ak_call" apps/api/` 应为空

```bash
# 验证收口
grep -rn --include="*.py" "^import akshare\|^from akshare" core apps tests
# 期望: 仅命中 core/integrations/akshare.py + akshare_worker.py
```

### 雷区 2:uvicorn `--reload` 不安全 + 服务必须配套重启

reload 时 V8 状态污染会 SIGABRT。**代码变更手动重启**,模板:

```bash
# 停三方 (redis 不停,docker-compose 管它)
pkill -9 -f "apps.collector.main" 2>/dev/null
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null
sleep 2

# ... 干活、改代码、commit、跑测试 ...

# 收尾:重启 collector + api,不要留任何端口空着
docker compose -f docker-compose.dev.yml up -d redis
nohup bash -c '. .venv/bin/activate && python -m apps.collector.main' >> /tmp/collector.log 2>&1 &
disown
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 8
curl -s -m 3 http://localhost:8787/api/health | grep -o '"status":"[^"]*"'
curl -s -m 3 http://localhost:8788/health | grep -o '"status":"[^"]*"'
```

**反模式**(已踩):e2e 测试末尾 `pkill -9` 收尾但忘了重启 → 用户报"加载失败"实际是没起来。**任何 `pkill` 必须配套 nohup 重启**。

**自己重启不让用户动手**。`api` 重启不影响 `collector` 采集(Plan 1 拆进程后),`collector` 崩则停所有 cron 务必同步重启。

### 雷区 3:bar 时间戳约定(intraday 富途口径 + 1d BJT 自然日)

**1d**:`ts = BJT 自然交易日 00:00` (= UTC `(D-1) 16:00`)。`bjtDateKey(ts)` 直接得到交易日,**不需要偏移**。
**Intraday(非 1m)**:`ts = bar close 时刻`(本市场 wall-clock → UTC):
- sina (A 股) `day` 字段已是 close → 直通
- Alpaca (美股) `b.timestamp` 是 START → 出口 `+freq` 转 close
- 60m / 4h 走 `core/services/intraday_aggregator.py` 按 `market_sessions.SESSIONS` 切桶 `(open, close]`
- 1m **不**改(详情页用,不入信号链路)

**给未来 agent**:`bar_ts` 直接喂 `bjtDateKey` / `toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'})`,**前端零偏移**。adapter 切源若新源是 START 语义,在出口 `ts + interval` 转 CLOSE。

### 雷区 4:FastAPI 路径参数顺序

```python
# ❌ /profiles 会被 /{symbol}/profile 吃掉
@router.get("/{symbol}/profile")
@router.get("/profiles")

# ✅ 具体路径在前
@router.get("/profiles")        # 先注册
@router.get("/{symbol}/profile")
```

`apps/api/routes/symbols.py` 踩过。

### 雷区 5:日志持久化与崩溃排查

**事实源**:`data/logs/api.log` / `api-errors.log`(WARNING+)/ `fault.log`(SIGABRT C 层栈)。`/tmp/api.log` 和 `/tmp/collector.log` 仅 stdout 镜像,**不是事实源**但因为 append 模式重启不丢。

`core/integrations/logging_setup.py::setup_logging()` 在 collector + api 启动早期各调一次。`faulthandler` fd 直写 fault.log,V8 SIGABRT 时 stdout 来不及 flush 但 fault.log 仍落盘。

```bash
tail -100 data/logs/api-errors.log   # 警告/错误
tail -50 data/logs/fault.log          # C 层崩溃栈(若有)
grep "ak_call.failed\|breaker.opened" /tmp/collector.log  # 中间件状态
```

---

## 代码规范(项目特有)

### 规范 1:单一事实源(SSoT)收口表

新增任何概念前**先看这张表,在 SSoT 内修改**。

| 概念 | SSoT 位置 |
|---|---|
| akshare 调用 | `core/integrations/akshare.py::ak_call` |
| ak_call 三层中间件 | `core/integrations/{breaker,ratelimit,outlets/}.py` |
| Redis key 命名 | `core/cache/keys.py`(所有 key 必须经此构造函数) |
| Redis 客户端封装 | `core/cache/redis_client.py::RedisCache`(msgpack + key 校验) |
| 交易日识别 | `core/domain/market_calendar.py::is_trading_day(market, when)` |
| Interval 元数据 | `core/domain/intervals.py::INTERVAL_CONFIG` |
| 前端 Interval tab | `apps/web/lib/intervals.ts` |
| 信号时间格式化 | `apps/web/lib/signal_time.ts` |
| 信号表格 UI | `apps/web/components/SignalsTable.tsx` |
| Symbol market 推断 | `core/domain/markets.py::infer_market` + `apps/web/lib/markets.ts::inferMarket`(前端镜像) |
| Stale 染灰 UI | `apps/web/components/StaleBadge.tsx` |
| Leader 状态(单例) | `core/scheduler/leader_gate.py::set_leader/is_leader` |
| 优化清单 | `docs/TODO.md`(跨会话单一事实源) |

**反模式**:发现两个文件出现相似 interval 列表 / 时间格式化 / 表格 JSX,立即抽 SSoT。

### 规范 2:Redis key 命名 4 大 namespace

所有 key 必须经 `core/cache/keys.py` 构造函数,**禁止散点字符串拼接**。4 个 namespace:

```
cache:*       热缓存层 (强制 TTL)
  cache:quote:{market}:{symbol}          90s
  cache:index:{symbol}:minute:{days}     90s
  cache:market:{m}:dashboard|top|ai_packet  120-240s
  cache:bars:{m}:{s}:{interval}:tail     300s
  cache:chip:{symbol}:{days}d            1800s

state:*       状态/锁 (无 TTL or 长 TTL)
  state:leader:collector                 15s(5s 续期)
  state:source:{sina|em|ths}             breaker 状态
  state:outlet:{id}                      banned_until
  state:inflight:{key}                   防穿透

bus:*         Redis Streams (MAXLEN 限内存)
  bus:quote.tick / bars.updated / signal.new / source.status
  bus:bars.refill_request                api → collector 按需补

ratelimit:*   Lua 令牌桶
  ratelimit:source:{sina|em|ths}
```

`keys.validate(key)` 在所有 `set_msgpack` / `get_msgpack` 调用前自动跑,unknown namespace 会 raise。

### 规范 3:Adapter Protocol + Service 分层

```
Route (薄,参数校验 + DTO)
  → Service (业务逻辑,DB/cache 读写)
    → Repo (纯 DB 读写) / RedisCache (纯 cache 读写)
    → Adapter (纯外部 API,collector 才用)
```

**禁止**:Route 直接调 Repo 或 Adapter。Service 是必经层。
**禁止**:api 进程的 Service 调用任何会触发 ak_call 的方法。统一用 `*_cache_only` 变体(如 `KLineService.get_bars_cache_only`、`ChipService.get_summary_cache_only`)。

### 规范 4:DB 引擎边界

- **Redis**:热缓存 + bus + 状态。**不持久化历史**
- **DuckDB**(`data/bars.duckdb`):历史 K 线时间序列,列存压缩
- **SQLite**(`data/state.db`,WAL):watchlist / signals / fund_flow / symbol_directory / notifications

**禁止**跨 DuckDB/SQLite join,在 Python 层做。

### 规范 5:错误处理 — 优雅降级不 Fail-Fast

```python
# 典型 service 模式
async def scan_many(self, symbols, interval):
    total = 0
    for sym in symbols:
        try:
            total += await self.scan_symbol(sym, interval)
        except Exception as e:  # noqa: BLE001
            log.warning("signal.scan_failed", symbol=sym, error=str(e))
    return total
```

api 路由 cache miss → 返回 `meta.stale=true` + 触发 `bus:bars.refill_request`,**绝不**当场 ak_call。

### 规范 6:日志结构化(structlog,event + kv)

```python
log.info("signal.scan_new", symbol=sym, interval=iv, new=n, total=len(records))
# 不要: log.info(f"scanned {sym} {iv}: {n} new")
```

`grep "signal.scan_new" /tmp/collector.log` 一行命中。

### 规范 7:测试约定

- 单测:`tests/unit/<layer>/test_<file>.py`,纯函数 / mock,`make test` 跑
- 集成:`tests/integration/`,带 `@pytest.mark.integration`,默认不跑
- Redis 测试:用 `fakeredis` fixture,不依赖真实 Redis
- 信号公式回归:用固化数据 fixtures(`tests/unit/indicators/fixtures/600519_daily.csv`),不依赖网络

---

## 数据流核心路径

| 数据 | 写者 | 读者 | 频率 | Redis key |
|---|---|---|---|---|
| **A 股 quote** | collector tick_snapshot(sina HTTP 直连,**不经 ak_call**) | api /quote | 10s | `cache:quote:ashare:*` |
| **美股 quote** | collector tick_snapshot(Alpaca latest_quote) | api /quote | 10s | `cache:quote:us:*` |
| **8 大指数 5m 序列** | collector index_minute job | api /indices/{s}/minute | 30s | `cache:index:*:minute:1` |
| **大盘聚合** | collector market_dashboard job | api /markets/{m}/dashboard | 60s | `cache:market:{m}:dashboard` |
| **涨跌幅榜** | collector market_top job | api /markets/{m}/top | 60s | `cache:market:{m}:top` |
| **AI 大盘** | collector ai_packet job | api /ai/ashare/market-packet | 60s | `cache:market:ashare:ai_packet` |
| **K 线 bars** | collector(fetch / refill 消费 bus) | api /symbols/{s}/bars(get_bars_cache_only) | 按需 | DuckDB,Redis 只 cache tail |
| **CD 信号** | collector cd:* cron(scan_cd_job) | api /cd-signals | 按交易日 cron | SQLite |
| **筹码摘要** | collector chip:preload(BJT 15:35) | api /symbols/{s}/chip_summary(cache_only) | 日终预热 | DuckDB |

**所有 cron 都经 `_leader_gated` 包裹** — 单机永远 leader,多节点只 leader 跑。

---

## 交易日识别(2026-05-28 集成)

`core/domain/market_calendar.py` 用 `exchange_calendars` 包:
- `XSHG`(A 股)、`XHKG`(港股)、`XNYS`(美股)各自独立日历,识别春节/独立日/清明/调休等
- crypto 永远 True
- `tick_snapshot_once` / `index_minute` / `market_top` / `ai_packet` 4 个高频 job **非交易日跳过**

```python
from core.domain.market_calendar import is_trading_day
if not is_trading_day("ashare"):
    return  # 节假日 / 周末跳过, 避免 ~30% sina + ~50% em 调用浪费
```

---

## ak_call 三层中间件(2026-05-27 集成)

每次 `ak_call` 顺序穿过:
1. **Breaker**(`core/integrations/breaker.py`)— per-source(sina/em/ths),60s 窗 60% 失败率 → open 5min → half-open 探针
2. **Ratelimit**(`core/integrations/ratelimit.py`)— Lua 令牌桶,sina 5/s burst 20,em 10/s burst 50,ths 3/s burst 10
3. **Outlet**(`core/integrations/outlets/`)— LocalOutlet 默认,未来接代理池零业务改动

状态全在 Redis,所有 collector 节点共享决策。`evaluate_response` 检测 sina banned 伪正常返回(单列 HTML)。

---

## 工作流约定

### 沟通

- **默认中文**(compaction 前后都是)。代码注释、log event 字符串保持中文
- **简洁汇报**:做了什么 + 关键验证 + 下一步建议
- **简单问题直接回答**,不开 plan、不建 task

### 非平凡任务标准流程

1. `EnterPlanMode` + `AskUserQuestion` 对齐(避免大改后被否)
2. `TaskCreate` 跟踪子任务,开始 `in_progress`,完 `completed`
3. 改完跑验证(下一节)
4. 简短汇报

### 改完的验证三步

```bash
# 1. 后端 import 测试(最快发现循环依赖/拼错)
. .venv/bin/activate && python -c "from apps.api.main import app; from apps.collector.main import app as a; print('OK')"

# 2. 前端类型检查
cd apps/web && npx tsc --noEmit

# 3. 全套单测 + 重启 + 业务冒烟
. .venv/bin/activate && pytest -m "not integration" -q
# (如改了 api/collector)按雷区 2 模板重启 collector + api
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8787/api/health
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8788/health
grep -c "ak_call.banned_signature\|breaker.opened" /tmp/collector.log  # 异常苗头
```

### Git 提交

- **按主题拆 commit**(chore / feat / fix / docs / refactor)
- commit message **中文**,body 列改动点。参考最近 5 个 commit 风格
- **不要 push**,不要修改 git config(身份/签名)
- 提交前确认 `data/*.db` 不在 staged 区(`.gitignore` 应已忽略)

### 调试与排查

- 优先看 `data/logs/api-errors.log` + `/tmp/collector.log`
- `grep "racer\." /tmp/collector.log` 看 ak_call mini_racer 锁等待
- `docker exec marketpulse-redis-dev redis-cli keys "state:*"` 看断路器/出口状态
- 重启服务**自己来**(用户不动手)

### 红线(不要做)

- ❌ 任何业务文件直接 `import akshare`(走 `ak_call`)
- ❌ **api 路由通过任何路径触发 `ak_call`**(直接 + service 层间接都不行)
- ❌ 给 `uvicorn` 加 `--reload`
- ❌ 跨 DuckDB / SQLite join
- ❌ 把分钟级 quote 写进 SQLite(用 Redis `cache:quote:*`)
- ❌ 让一个 symbol 的失败把整个 batch 拖死(单条 try/except)
- ❌ Redis key 散点字符串拼接(经 `core/cache/keys.py`)
- ❌ 给 dev 强行 fail-fast(原则 2 优雅降级)
- ❌ `pkill` 不配套 nohup 重启(雷区 2 反模式)
- ❌ push 到远端 / 修改 git 身份(除非明确授权)

---

## 进一步阅读

1. **`docs/TODO.md`** — 已识别未实施的优化,分进度 + 价值×代价
2. **`docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md`** — Plan 1+2+3 完整设计(957 行)
3. **`docs/superpowers/specs/2026-05-13-marketpulse-design.md`** — 项目原始 spec
4. **`docs/superpowers/plans/2026-05-27-stable-data-plan-{1,2,3}-*.md`** — Plan 各阶段细节
5. **`docs/third_Indicator/`** — 富途指标参考(`core/indicators/cd.py` 翻译自 `CD.ftindex`)
6. **`~/.claude/projects/-Users-xiangrong-stock-marketpulse/memory/`** — 用户偏好(`MEMORY.md` 索引,自动加载)

---

## 当前活跃约束(2026-05-28)

- **进程拆分已完成**:collector(8788)+ api(8787)+ redis(6379,docker)+ web(3000)
- **apps/api/ 真正 0 ak_call**(直接 + 通过 service 间接)— 一切读路径走 Redis cache
- **HK 指数 collector job 暂未实装** — `/api/indices/HSI.HK/minute` 等返回 `stale=true, reason="hk_index_collector_pending"`,Plan 4 候选
- **Crypto coingecko 现在限流**(HTTP 429),`tick:crypto` 大量 failed — adapter 改 Binance Spot API 在 TODO
- **CD 信号 1d 有时连续几天无新信号**:不是 bug,公式特性(底/顶背离低频事件)
- **scheduler 每 10s 读一次 sqlite 拿 watchlist**:浪费但单读 <1ms 可忽略
- **`acknowledged` 字段** 后端建好但 UI 没用:死代码,留待"已读"功能或删
- 美股数据走 Alpaca SIP feed(2026-05-21 切换),前复权 split + dividend 已回算。如发现"价格跳变"先检查 split 日,看 `core/adapters/us.py::_fetch_history_alpaca` 的 `adjustment` 参数
- 美股 4h tab 已启用,scheduler `cd:us:4h` ET 08:05/12:05/16:05/20:05 跑 4 次/日
