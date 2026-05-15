# CLAUDE.md

> 给 Claude(或任何新接手 agent)的项目上下文。第一次进项目从这里开始读,5 分钟内能上手。

## 项目是什么

**MarketPulse** — 个人本地市场监控 + 选股策略平台。后端 FastAPI(8787) + 前端 Next.js(3000) + DuckDB(K 线) + SQLite(state) + APScheduler。覆盖 A 股 / 港股 / 美股 / Crypto 四市场。

**用户**:`zhonghuai`(中国境内,A 股口径,默认中文沟通)。

**当前里程碑**:Plan 1 + Plan 2 + Plan 2.5 已交付。详见:
- `docs/superpowers/specs/2026-05-13-marketpulse-design.md`(整体设计 + 路线图)
- `docs/superpowers/plans/2026-05-13-marketpulse-plan-{1,2}-*.md`(顶部有完成状态总览)
- `docs/TODO.md`(已识别但未实施的优化点 — 单一事实源)

## 必读雷区

### 1. py_mini_racer 0.6.0 是结构性风险

PyPI 上最新版,**项目已停更**。akshare 的 sina 系接口(`stock_zh_a_minute`、`fund_etf_*sina`、`stock_sector_*`、`stock_zh_a_spot` 等)内部用 py_mini_racer 解 JS。该版本在 macOS arm64 有 V8 析构 race,**即使顺序调用也概率性 SIGABRT**,worker 直接死掉,端口请求全 ECONNRESET。

**强制约束**:任何 ak 调用**只能**经 `core/integrations/akshare.py::ak_call(name, *args, caller, **kwargs)`,内部统一锁+日志。

```python
# 正确
from core.integrations.akshare import ak_call
df = await ak_call("stock_zh_a_minute", symbol=..., period=..., caller="my_module:600519.SH")

# 禁止
import akshare as ak
df = await asyncio.to_thread(ak.stock_zh_a_minute, ...)  # ❌ 会埋雷
```

**验证**:`grep -rn --include="*.py" "^import akshare\|^from akshare" core apps tests` 应该只命中 `core/integrations/akshare.py`。

**崩溃征兆**:`/tmp/api.log` 末尾出现 `[FATAL:address_pool_manager.cc(67)] Check failed: !pool->IsInitialized()` + `libmini_racer.dylib` 栈。**不是网络问题**。

**排查命令**:`grep "racer\." /tmp/api.log | tail -20` 看最后一条 `caller=ak:xxx` 是谁触发的。

**根治方案**(未做):用 ProcessPoolExecutor 把 ak 调用放子进程,见 `docs/TODO.md`。

### 2. uvicorn `--reload` 不安全

`--reload` 重启 worker 时 V8 状态污染会触发 SIGABRT。`Makefile dev` 已经去掉 `--reload`,代码变更后**手动重启**:

```bash
pkill -9 -f "uvicorn apps.api.main:app"
cd /Users/xiangrong/stock/marketpulse
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
```

调试期间需要重启服务时,**自己执行**这套命令,不要让用户手动操作。

### 3. SQLite 与 DuckDB 共存

- `data/state.db` — sqlite,存 watchlist / signals / sectors / fund_flow / symbol_directory(已开 WAL)
- `data/bars.duckdb` — duckdb,存历史 K 线

**不要跨引擎 join**,在 Python 层做。

### 4. 1d bar 时间戳偏移

sina 给的 daily bar `ts = 收盘日 16:00 UTC = BJT 次日 00:00`。前端 `apps/web/lib/signal_time.ts::effectiveTsIso` 在显示层 -8h 还原收盘当日。**这是 workaround**,真正的修法是 adapter 层 normalize(见 `docs/TODO.md` 高价值/高代价那项)。

### 5. directory bootstrap 启动跳过

`apps/api/main.py` lifespan 里:`stock_zh_a_spot` 会污染 V8 状态,所以**只在 directory 表 < 100 行才刷新**。副作用:新上市/改名股票永远查不到。子进程隔离做完后撤这个 workaround。

## 单一事实源

| 概念 | 文件 | 用法 |
|------|------|------|
| ak 调用 | `core/integrations/akshare.py::ak_call` | 任何 ak.* 入口 |
| Interval 元数据 | `core/domain/intervals.py::INTERVAL_CONFIG` | lookback / bars_per_day / 是否信号 / crypto-only |
| 前端 Interval 镜像 | `apps/web/lib/intervals.ts` | tab 渲染逻辑 |
| 信号时间格式化 | `apps/web/lib/signal_time.ts` | 1d -8h、BJT 自然日切分 |
| 信号表格组件 | `apps/web/components/SignalsTable.tsx` | 详情页 + 关注页共用 |
| mini_racer 锁 | `core/services/_locks.py::acquire` | 仅在 `ak_call` 内部使用 |

加新周期 / 加新 ak 接口前先看这些文件,**不要在多处散点写**。

## 架构与目录速览

```
core/
├── adapters/           # 4 市场 adapter,统一 Protocol
├── cache/              # QuoteCache (memory + TTL)
├── domain/
│   ├── models.py       # Quote/Bar/IndicatorSignal/Watchlist/...
│   └── intervals.py    # ⭐ Interval 单一事实源
├── indicators/cd.py    # CD 抄底/卖出指标公式
├── integrations/
│   └── akshare.py      # ⭐ ak 调用唯一入口
├── persistence/        # duckdb_repo + sqlite repos
├── scheduler/          # tick + signal cron jobs
└── services/           # KLine / Watchlist / Sector / FundFlow / Signal scan
apps/
├── api/                # FastAPI routes + DI + WS
├── web/                # Next.js 14 App Router
└── warmup.py           # CLI 首次回填
docs/
├── TODO.md             # ⭐ 优化清单,跨会话单一事实源
├── superpowers/        # spec + plan 文档
└── third_Indicator/    # 富途指标参考资料 (.ftindex 源 + PDF)
```

## 工作流约定

### 沟通

- **默认中文**(不论 compaction 前后)。代码注释、log 字符串保持中文(项目惯例)
- 改动后**简洁汇报**:做了什么、关键验证结果、下一步建议(若有)。不要长篇总结

### 调试与重启

- API/Web 异常先看 `/tmp/api.log` 末尾,**有 `FATAL` 就是 mini_racer 崩了**
- 重启服务**自己来**,不要让用户手动:`pkill -9` + `nohup` 启动
- background task 启动后等几秒 + curl 验证 200 再回报

### 修改后的验证

- 后端改:`. .venv/bin/activate && python -c "from apps.api.main import app"` 至少 import 通
- 前端改:`cd apps/web && npx tsc --noEmit` 必跑
- 业务改:重启 API 后 `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8787/api/health` 等关键接口冒烟

### Git

- 一次任务一组 commit,**按主题拆**(chore / feat / docs)
- commit message **中文 + body 详细列改动**(参考 `git log --oneline -5` 的风格)
- **不要** push,除非用户明确要求。**不要** `git config` 修改身份

### Plan 模式

- 非平凡任务先用 `EnterPlanMode` + `AskUserQuestion` 对齐再动手 — 避免大改后被否
- TODO 优化项分**价值 × 代价**优先级,默认先吃"高价值/低代价"

### Skill 调用

- 用户输入 `/<skill-name>` 才调用对应 skill。其它情况依赖普通工具

## 当前活跃约束(状态时间 2026-05-15)

- **CD 信号公式输出在 2026-05-15 后没有新 1d 信号**:不是 bug,公式特性,见用户教材 `docs/third_Indicator/`
- **关注页 4h tab** 仅在 watchlist 含 crypto 标的时显示(股票市场 4h ≡ 1d)
- **scheduler 每 10s 读一次 sqlite 拿 watchlist**(见 `core/scheduler/jobs.py::tick_snapshot_once`),性能上是浪费但单读 <1ms 可忽略,优化项在 TODO

## 我之前踩过的具体坑(供后人避免)

1. **批量 profile 路由顺序**:`/profiles` 必须在 `/{symbol}/profile` 之前注册,否则会被路径参数吃掉
2. **`asyncio.Lock` 不能跨 to_thread 边界保护并发性** — 但 ak 入口只要走 `ak_call` 就强制串行
3. **删 `SignalInterval` 类型时**忘了 `app/symbol/[code]/page.tsx` 里的 `as SignalInterval` cast,要改成不传 cast(`listCDSignalsBySymbol` 签名是 string[])
4. **类型 shadow 问题不存在**:虽然写了 `core/integrations/akshare.py`,Python 顶层 `import akshare` 走 sys.path 优先级,不会 import 到自己
5. **TaskStop 会带走子进程**:用 `Bash run_in_background` 起的 uvicorn,如果在 background bash 里 nohup 了,主 Bash 任务被 stop 时 uvicorn 也会一起死

## 不要做

- 不要直接 `import akshare`(走 ak_call)
- 不要给后端 dev 加 `--reload`
- 不要在 ScanBody / 任何接口默认值里硬编码 interval 列表(用 `SIGNAL_INTERVALS`)
- 不要假设"今天没信号"是 bug,先用 `python scratch/verify_pipeline.py` 跑一次公式
- 不要为修一个小问题做大重构,**走 TODO 列表的优先级**

## 进一步阅读

- `docs/TODO.md` — 14 项未做的优化,按 价值×代价 分组
- `docs/superpowers/specs/2026-05-13-marketpulse-design.md` — 整体设计 + 验收标准
- `~/.claude/projects/-Users-xiangrong-stock-marketpulse/memory/` — 用户偏好 + 关键约束(已自动加载到上下文)
