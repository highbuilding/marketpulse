# MarketPulse 踩坑实录与代码规范(详版)

> CLAUDE.md 的配套详版。CLAUDE.md 只留"必须遵守的结论",踩坑的来龙去脉、完整规范在这里。

更新日期: 2026-06-18

---

## 必读雷区(踩过的坑)

### 雷区 1:py_mini_racer V8 race(已根治)

**症状**:`SIGABRT` + `libmini_racer.dylib` 栈 → worker 死,`ECONNRESET`。
**根治**:akshare 调用全走 `ak_call` → `_run_ak_in_child_process` 子进程隔离(子进程崩主进程不受影响)+ 三层中间件熔断。全局 `_racer_acquire` 锁仍作 watchdog(>60s dump 线程栈到 `fault.log`)。

**今天的约束**:
- 不允许任何业务文件 `import akshare`,走 `core/integrations/akshare.py::ak_call`。
- **api 路由不允许任何 ak_call**(直接 + 通过 service 间接)。

```bash
grep -rn --include="*.py" "^import akshare\|^from akshare" core apps tests
# 期望: 仅命中 core/integrations/akshare.py + akshare_worker.py
grep -rn "ak_call" apps/api/   # 应只命中注释字符串
```

### 雷区 2:uvicorn `--reload` 不安全 + 服务必须配套重启

reload 时 V8 状态污染会 SIGABRT。**代码变更手动重启**。`pkill` 必须配套 nohup 重启(反模式:e2e 末尾 pkill 忘了重启 → 用户报"加载失败")。重启模板见 CLAUDE.md《工作流》。

- stdout 镜像用 `>`(启动截断)而非 `>>`(append 永涨,曾涨到 4.5GB)。`/tmp/*.log` 非事实源,事实源在 `data/logs/`(已 rotate)。
- api 重启不影响 collector 采集;改某市场 collector 只重启那一个(进程隔离);web 改前端自动热更不用重启。
- **自己重启,不让用户动手。**

### 雷区 3:bar 时间戳约定(intraday 富途口径 + 1d BJT 自然日)

- **1d**:`ts = BJT 自然交易日 00:00`(= UTC `(D-1) 16:00`)。`bjtDateKey(ts)` 直接得交易日,前端零偏移。
- **Intraday(非 1m)**:`ts = bar close 时刻`。sina(A股)`day` 已是 close 直通;Alpaca(美股)`timestamp` 是 START → 出口 `+freq` 转 close;60m/4h 走 `intraday_aggregator.py` 按 `market_sessions.SESSIONS` 切桶 `(open, close]`。1m 不改(详情页用,不入信号链路)。
- **crypto 例外**:ts 用 open 对齐(见 memory `project_crypto_open_aligned`)。
- adapter 切源若新源是 START 语义,在出口 `ts + interval` 转 CLOSE。

### 雷区 4:FastAPI 路径参数顺序

具体路径必须在 `/{param}` 之前注册,否则被吃掉。`apps/api/routes/symbols.py` 踩过。

```python
@router.get("/profiles")        # ✅ 先注册具体路径
@router.get("/{symbol}/profile")
```

### 雷区 5:日志持久化与崩溃排查

**事实源在 `data/logs/`**(已按大小 rotate):
- `api.log` / `api-errors.log`(WARNING+)— api 进程
- `collector_{market}.log` / `*-errors.log` — collector 进程
- `fault.log`(SIGABRT/SIGSEGV C 层栈,append 不 rotate,共享;faulthandler fd 直写,V8 崩溃时 stdout 来不及 flush 但它仍落盘)

`logging_setup.py::setup_logging(process_name=...)` 在每个进程启动早期调一次,决定文件名前缀。

```bash
tail -100 data/logs/api-errors.log
tail -100 data/logs/collector_ashare-errors.log   # 含 ak_call.failed/empty/timeout
grep "ak_call.failed\|breaker.opened" data/logs/collector_ashare.log
```

### 雷区 6:DuckDB 单写多读互斥 → api 绝不直连,历史走 collector 转发

DuckDB 同一文件**同一时刻要么"一个 RW 独占",要么"多个 read_only 共享",不能并存**。api 若 read_only 直连 `bars_{market}.duckdb`:回填期 collector 持 RW → api 读 100% 失败(`Conflicting lock`);稀疏写时 api 读会把 collector 的写踢掉丢数据。这就是 api 一刀切脱离 DuckDB 的根因。

**方案**:collector 进程内用自己持的 RW `bar_repo` 查 `fetch_history_paged`(零锁冲突),内嵌 FastAPI 挂 `GET /internal/bars/history`(`apps/collector/base.py::attach_bars_history_route`,**module 级挂载**,lifespan 内挂 Starlette 不认会 404)。api `/api/symbols/{s}/bars/history` 用 httpx 转发到 `127.0.0.1:{8788|8790}`(`trust_env=False` 绕 7890 代理),不可达则 `stale` 降级。

- 任何"api 读 DuckDB 历史"一律走转发,别给 api 开 read_only。
- 游标分页(反向翻页):`before` 空=最新页;返回严格早于 `before` 的最近 `limit` 根,升序;不足一页=到上市首日。
- 实时与历史两通道解耦:SSE 只推最右一根;历史走 REST 分页。分时图同理走独立库 `intraday_{market}.duckdb`(物理隔离规避本雷区)。

---

## 代码规范(详版)

### Redis key 4 大 namespace(经 `core/cache/keys.py` 构造,禁止散点拼接)

```
cache:*       热缓存层 (强制 TTL): quote / index / market:{dashboard,top,changes,board_changes} / bars:*:tail / chip / intraday:*:current
state:*       状态/锁: leader:collector_{market} / source:{sina|em|ths}(breaker) / outlet / inflight / subscribe
bus:*         Redis Streams (MAXLEN 限内存): quote.tick / bars.updated / signal.new / live.message / intraday.updated / bars.refill_request / collector.symbols.changed
ratelimit:*   Lua 令牌桶: source:{sina|em|ths}
```

`keys.validate(key)` 在 set/get_msgpack 前自动跑,unknown namespace raise。

### 分层:Route(薄,校验+DTO)→ Service(业务,DB/cache 读写)→ Repo / RedisCache / Adapter(collector 才用)

- 禁止 Route 直接调 Repo/Adapter,Service 是必经层。
- 禁止 api 进程的 Service 触发 ak_call,用 `*_cache_only` 变体(`KLineService.get_bars_cache_only` 等)。

### DB 引擎边界

- **Redis**:热缓存 + bus + 状态,不持久化历史。
- **DuckDB**(`data/bars_{market}.duckdb` / `intraday_{market}.duckdb`):K 线/分时时间序列,列存压缩。批量写用 DataFrame(见 memory `project_duckdb_bulk_upsert`)。
- **SQLite**(`data/state.db`,WAL):signals / limit_pool / theme_snapshots / live_messages / daily_reviews / 候选池 / 回测 / 纸面指令 / watchlist / fund_flow / directory / notifications。
- **禁止跨 DuckDB/SQLite join**,在 Python 层做。

### 错误处理:优雅降级不 Fail-Fast

单条 try/except,一个 symbol 失败只 warning 不拖垮 batch。api cache miss → `meta.stale=true` + 触发 `bus:bars.refill_request`,绝不当场 ak_call。任何缺口写 `data_gaps`,不把缺数据伪装成结论。

### 日志结构化(structlog,event + kv)

```python
log.info("signal.scan_new", symbol=sym, interval=iv, new=n)   # ✅
# 不要 f-string 拼整句
```

### 测试约定

- 单测 `tests/unit/<layer>/test_<file>.py`(纯函数/mock,`make test` 跑);集成 `tests/integration/` 带 `@pytest.mark.integration` 默认不跑。
- Redis 测试用 `fakeredis`;信号公式回归用固化 fixtures(`tests/unit/indicators/fixtures/`),不依赖网络。

---

## ak_call 三层中间件

每次 `ak_call` 顺序穿过(状态全在 Redis,所有 collector 共享决策):
1. **Breaker**(`breaker.py`)— per-source(sina/em/ths),60s 窗 60% 失败率 → open 5min → half-open 探针。
2. **Ratelimit**(`ratelimit.py`)— Lua 令牌桶,sina 5/s burst 20,em 10/s burst 50,ths 3/s burst 10。
3. **Outlet**(`outlets/`)— LocalOutlet 默认,未来接代理池零业务改动。

`evaluate_response` 检测 sina banned 伪正常返回(单列 HTML)。瞬时网络错误(SSLError/EOF/超时)内层重试 2 次,banned/限频/空数据不重试。

## 交易日识别

`core/domain/market_calendar.py::is_trading_day(market, when)` 用 `exchange_calendars`(XSHG/XHKG/XNYS 各自日历 + crypto 永真)。高频 job 非交易日跳过,避免无谓 sina/em 调用。
