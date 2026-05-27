# 稳定数据采集 + 快速读取 设计文档

> 状态: 设计阶段 (brainstorming → writing-plans 之间)
> 日期: 2026-05-27
> 范围: A 股为主, 兼顾港股 / 美股 / Crypto 的统一采集层
> 决策: 单机部署, 留好横向扩展接口; Redis + 消息总线; 进程拆分 (collector / api)

---

## 0. 背景与目标

### 0.1 现状痛点

1. **A 股数据采集服务不稳定, 经常报错**
   - sina `IndexError: list index out of range` (JS 解析失败) 高频
   - 东财 `timeout after 8s` 中频
   - mini_racer V8 race / hang 偶发 (雷区 1)
2. **前端访问慢**
   - K 线分时图加载慢 (5~15s)
   - 大盘数据加载慢 (首屏多接口串行)
   - 重复访问同一标的 K 线还是慢 (cache miss bug)
   - 分时数据频繁 500 报错
3. **采集与 API 共进程互相拖累**
   - 全局 mini_racer 锁让前端请求与 scheduler 任务争抢同一把锁
   - 任何一个 ak_call 慢都会阻塞所有正在进行的 HTTP 请求

### 0.2 目标

1. 稳定的 A 股数据采集 — 消除 "采集与 API 共进程" 根因
2. 职责清晰的采集服务 — 只做 "定时拉、写库、必要的整合计算" 三件事
3. 前端读取快 — read 路由 p95 < 300ms, p99 < 500ms
4. 单机部署, 留好横向扩展接口 — 未来扩展不需要重写
5. 应对 IP ban 与限流 — 出口可切换, 业务可降级

### 0.3 设计原则 (沿用项目 spec 第 0 章)

1. 开源 + 免费优先 (akshare / Redis OSS)
2. 优雅降级, 不 fail-fast
3. 国内可用
4. 决策支持非执行
5. 单一可跑 (V1 不引入 Prometheus / Grafana / 多实例 / 告警)

### 0.4 关键决策记录 (brainstorming 期间确认)

| 维度 | 选择 |
|---|---|
| 部署形态 | 单机, 留好横向扩展接口 (排除主备热备 / 中央服务 / 无中心分片) |
| A 股数据源 | 只用 akshare, 不引入第三方降级源 |
| IP ban 应对 | 出口管理器 (Outlet 抽象), 当前本地直连; 未来可接商业代理池 |
| 读侧缓存 | Redis + 消息总线 (重量级, 一步到位) |
| 总线实现 | Redis Streams (零额外依赖) |
| 限速器 | 纯 Lua 脚本 + Redis 令牌桶 (排除 redis-cell module) |
| 序列化 | 缓存 msgpack, bus JSON |
| 实时推送 | SSE (排除 WebSocket) |
| dev 启动 | honcho + Procfile |
| 容器 | Redis 跑 docker-compose |
| 前端范围 | 本次只做 stale meta 染灰, SSE 推迟到下一个 Plan |

---

## 1. 进程拓扑总览

### 1.1 目标拓扑

```
┌─────────────────────────────────────────────────────────┐
│                  本机 (单机部署)                         │
│                                                          │
│  ┌──────────────────┐         ┌──────────────────┐      │
│  │ apps/collector/  │         │   apps/api/      │      │
│  │   main.py        │         │   main.py        │      │
│  │                  │         │                  │      │
│  │ - APScheduler    │         │ - FastAPI 路由   │      │
│  │ - ak_call (唯一) │         │ - 只读 Redis/DB  │      │
│  │ - Outlet 管理    │         │ - 绝不调 ak_call │      │
│  │ - Leader 抢锁    │         │ - SSE 端点(后续) │      │
│  └────────┬─────────┘         └────────▲─────────┘      │
│           │                            │                 │
│           │ 写                         │ 读              │
│           ▼                            │                 │
│  ┌──────────────────────────────────────────────┐       │
│  │  Redis (本机 docker-compose)                 │       │
│  │  - 热缓存层 (quote/index/dashboard, TTL)     │       │
│  │  - 消息总线 (Streams: bus:quote.tick / ...)  │       │
│  │  - 状态/锁 (outlet/source/leader)            │       │
│  │  - 分布式限速器 (per-source 令牌桶, Lua)     │       │
│  └──────────────────────────────────────────────┘       │
│           │                            │                 │
│           ▼                            ▼                 │
│  ┌──────────────────┐         ┌──────────────────┐      │
│  │ DuckDB (bars)    │         │ SQLite (state)   │      │
│  │ - 历史 K 线归档  │         │ - 信号/关注/板块 │      │
│  │ - collector 写  │         │ - source_status  │      │
│  │ - api 只读       │         │ - 双方都写       │      │
│  └──────────────────┘         └──────────────────┘      │
│                                                          │
│           ▲                                              │
│           │ 启动管理                                     │
│       honcho (dev) / systemd (生产)                      │
└──────────────────────────────────────────────────────────┘
                         ▲
                         │ HTTP / SSE
                         │
              ┌──────────┴──────────┐
              │   apps/web (Next)   │
              └─────────────────────┘
```

### 1.2 进程职责硬约束

| 进程 | 允许做的 | **绝对禁止** |
|---|---|---|
| **collector** | scheduler / ak_call / 写 DB / 发 bus 事件 / 维护 outlet+source 状态 / 预聚合写 cache | 暴露任何 HTTP 业务接口 (只暴露 `/health` 给运维) |
| **api** | 读 Redis / 读 DB / 订阅 bus / SSE 推前端 / 轻量 CPU 计算 | 任何形式的 ak_call; 任何 mini_racer 锁等待; 写 K 线 bars 到 DuckDB |
| **redis** | 缓存 / 总线 / 状态 / 锁 / 限速器 | 持久化历史 K 线 (那是 DuckDB 的活) |

**这条约束是后续所有方案的硬底**。code review 时 P0 检查项: `grep -rn "from core.integrations.akshare import ak_call" apps/api/` 必须为空。

### 1.3 dev 启动方式

`Procfile`:
```
redis:     docker-compose -f docker-compose.dev.yml up redis
collector: . .venv/bin/activate && python -m apps.collector.main
api:       . .venv/bin/activate && uvicorn apps.api.main:app --port 8787
web:       cd apps/web && pnpm dev
```

`make dev` 调用 `honcho start`. 单进程拆双进程, 由 honcho 统一拉起 + 信号传播 + 日志聚合.

### 1.4 横向扩展接口 (留好但不实现)

| 扩展场景 | 当前做法 | 升级路径 |
|---|---|---|
| collector 多机 | 单实例, Leader 抢锁永远成功 | 抢 Redis SETNX 锁, 只 leader 跑 cron |
| api 多机 | 单实例, 直读本机 Redis/DB | api 改读远程 Redis; DB 升级共享存储 |
| 出口多 IP | 单一 LocalOutlet | 接代理池子类 (KuaidailiOutlet 等), 业务零改 |
| 跨机房热备 | 不支持 | leader 抢锁保证只有 1 个 collector 跑 cron |

---

## 2. Redis 用途分层 + 消息总线

### 2.1 Redis 4 类用途, key 命名空间不重叠

```
┌─────────────────────────────────────────────────────┐
│ Redis (单实例, db=0, 本机)                          │
├─────────────────────────────────────────────────────┤
│ ① 热缓存层    cache:<scope>:<key>     TTL 强制      │
│ ② 消息总线    bus:<topic>             Streams       │
│ ③ 状态/锁     state:<scope>:<key>     无 TTL or 长 │
│ ④ 限速器      ratelimit:<source>      Lua 令牌桶    │
└─────────────────────────────────────────────────────┘
```

**约束**: 任何 key 必须 `<scope>:<key>` 双段以上, 写入方必须设 TTL (除明确 "持久状态" 外). 审查时 `KEYS *` 一行能看出有没有人乱用.

### 2.2 ① 热缓存层

| Key | 写入方 | 写入时机 | TTL | 读取方 | 数据 |
|---|---|---|---|---|---|
| `cache:quote:{market}:{symbol}` | collector | tick 后批量写 | 90s | api | latest quote |
| `cache:index:{symbol}:minute` | collector | 每 30s | 90s | api | 当日 5min 序列 |
| `cache:market:{m}:dashboard` | collector | 每 60s | 120s | api | 整个市场页所需 |
| `cache:bars:{market}:{symbol}:{interval}:tail` | collector | bars 落库后 | 300s | api | 最近 N 根 bar |
| `cache:bars:{market}:{symbol}:{interval}:full:{hash}` | api 旁路 | 拉过整段后回写 | 600s | api | 完整 K 线段 |
| `cache:fundflow:{symbol}:30d` | collector | ff:symbols 后 | 1800s | api | 30 天资金流 |

**关键设计点**:
- **粗粒度键** — 把 "前端一次想要的整段数据" 打一个包 (msgpack), 减少 round-trip
- **collector 主动 push, api 被动读** — api 永不因 cache miss 触发 ak_call. miss 时返回 `meta.stale=true` + 触发 `bus:bars.refill_request`
- **double-cache** — api 进程在 Redis 之上加一层 50ms TTL in-process LRU, 扛 "前端同时打 100 次相同请求"

### 2.3 ② 消息总线 (Redis Streams 5 个 topic)

```
bus:quote.tick                     高频, MAXLEN 1000
  payload: {market, symbols: [{symbol, price, change_pct, ts}, ...]}
  发布: collector tick 后, 仅含变化的 symbol
  订阅: api → SSE 推前端 (Stage 6 之外)

bus:bars.updated                   中频, MAXLEN 500
  payload: {market, symbol, interval, last_ts, count}
  发布: collector 落库后
  订阅: api → 失效进程内 LRU + 通知前端

bus:signal.new                     低频, MAXLEN 200
  payload: {market, symbol, signal_id, kind, interval, ts}
  发布: collector 信号扫描产出新信号
  订阅: api → 通知前端 "新信号" 角标

bus:source.status                  极低频, MAXLEN 100
  payload: {source, old_status, new_status, fail_rate, since}
  发布: 熔断器状态切换 (collector)
  订阅: api → 推前端 "数据源降级" 提示

bus:bars.refill_request            按需, MAXLEN 100
  payload: {market, symbol, interval, days, requester_id}
  发布: api 发现 cache miss + DB 不全
  订阅: collector → 拉数据 + 写库 + 写 cache + 发 bars.updated
```

**为什么 Streams 不 pub/sub**:
- pub/sub 是 fire-and-forget, api 启动期事件丢失
- Streams 有持久化 (MAXLEN 限内存) / consumer group / ack 机制
- 零额外依赖 (Redis 内置)

### 2.4 ③ 状态/锁层

| Key | 类型 | TTL | 用途 |
|---|---|---|---|
| `state:leader:collector` | string | 15s (5s 续期) | 单机永远续期成功; 多节点抢锁 |
| `state:source:{name}` | hash | 无 | sina/em/ths 当前状态 + 失败原因/时间/失败率 |
| `state:outlet:{id}` | hash | 无 | 出口 IP/代理 状态 (ok/banned/cooling) |
| `state:inflight:{key}` | string | 短 TTL | 防穿透: 多请求同时来只 1 个真去拉 |

### 2.5 ④ 限速器 (纯 Lua + Redis 令牌桶)

```
ratelimit:source:sina      令牌桶: 5 tok/s, burst 20
ratelimit:source:em        令牌桶: 10 tok/s, burst 50
ratelimit:source:ths       令牌桶: 3 tok/s, burst 10
ratelimit:outlet:{id}      每出口独立配额 (商业代理按计费)
```

实现: 纯 Lua 脚本 (50 行) `EVAL` 一次拿 token, 原子. `core/integrations/ratelimit.py` 封装. 排除 redis-cell module (装起来麻烦, 单机 docker 镜像不带).

### 2.6 序列化格式

- 热缓存: **msgpack** (比 JSON 小 30%, 解码快 2 倍, `ormsgpack` 库)
- 总线消息: **JSON** (易调试, Streams `XADD` 字段直接 KV)
- DB: 不变 (DuckDB 列存 / SQLite 行)

### 2.7 故障兜底 (缓存层)

| 故障 | 处理 |
|---|---|
| Redis crash | api fallback 直读 SQLite/DuckDB, 前端染灰 |
| Redis 内存满 | MAXLEN + TTL 驱逐, 监控 `INFO memory` |
| collector 没启 | cache TTL 过期后空 → api stale + DB fallback |
| bus 堆积 | MAXLEN 裁剪, ack 跟不上丢老消息 |

---

## 3. 读路径重构 (对症 4 个卡顿)

### 3.1 总原则

所有 read 路由必须满足:
1. 不持有任何 mini_racer 锁
2. 不调 ak_call
3. p95 < 100ms (cache) / p99 < 300ms (DB)

不能满足的接口要么改为读 cache, 要么返回 stale + 后台 refill.

### 3.2 症状 1: K 线分时图慢 — `/api/indices/{symbol}/minute`

**现状** (`apps/api/routes/indices.py:77,107`): 路由层直接 `await ak_call(...)` 阻塞用户请求.

**改造**:
```python
@router.get("/{symbol}/minute")
async def index_minute(symbol, days=1, cache=Depends(get_redis_cache)):
    key = f"cache:index:{symbol}:minute:{days}"
    payload = await cache.get_msgpack(key)
    if payload:
        return payload
    fallback = await cache.get_last_known(key)
    await cache.publish_refill_request("index_minute", symbol, days)
    return {**fallback, "meta": {"stale": True, "data_age_seconds": ...}} \
        if fallback else {"points": [], "meta": {"stale": True, "reason": "warming_up"}}
```

**collector 侧** (`apps/collector/jobs/index_minute.py` 新增):
- 交易时段 BJT 09:30-15:00 每 30s, 非交易时段每 5min
- 一次循环把 8 个指数全拉, 失败标 source_status, 不阻塞下一次循环

**收益**: p95 从 5~15s → < 10ms

### 3.3 症状 2: 大盘数据慢 — `/market` 页

**现状**: 8 个指数 + 概览 + 资金流 + 关注列表分别多次 ak_call.

**改造**: 预聚合 API + collector 预先打包.

新增 `GET /api/markets/{m}/dashboard`, 一次返回前端所需:
```json
{
  "indices": [{symbol, name, minute_series, latest_quote}, ...],
  "overview": { up_count, down_count, total_amount, ... },
  "north_flow": { today_net, recent_30d },
  "hot_sectors": [...],
  "meta": { "fresh_at": "...", "stale": false, "missing_sections": [] }
}
```

collector 每 60s 跑 `compute_dashboard(market)` 写 `cache:market:ashare:dashboard`.

**部分降级**: 某个 section 拉失败 → 该字段 null + 写 `meta.missing_sections`, 前端染灰该卡片, 不影响其他.

**收益**: 8~10 串行请求 → 1 个请求 < 50ms

### 3.4 症状 3: 重复访问 K 线还是慢 — `/api/symbols/{s}/bars`

**根因** (`core/services/kline_service.py:64-69`):
```python
metrics_missing = (
    market == "ashare" and cached and covers
    and self._missing_ashare_daily_metrics(cached)  # ← 元凶
)
```
A 股 daily 近 20 根任一缺 amount/turnover → 整段重拉. 某些标的 amount 永远 None → 永远 cache miss.

**改造点 1**: 缺字段不该触发 cache miss
- 拆 "覆盖性" (数据范围够不够) vs "完整性" (字段全不全)
- `covers=True && fields_partial=True` → 命中, 返回 `meta.partial=true`
- 修补 amount/turnover 是 collector 的活, api 不管

**改造点 2**: API 层加 Redis 前置缓存
```python
@router.get("/{symbol}/bars")
async def bars(symbol, interval, days, cache, svc):
    cache_key = f"cache:bars:{market}:{symbol}:{interval}:tail"
    if days <= 365:
        cached = await cache.get_msgpack(cache_key)
        if cached:
            return cached
    bars = await svc.get_bars_cache_only(symbol, interval, start, end)
    if bars:
        return ...
    await cache.publish_refill_request("bars", symbol, interval, days)
    return {"bars": [], "meta": {"stale": True, "reason": "warming_up"}}
```

**改造点 3**: KLineService 拆双轨
- `get_bars_cache_only`: 只读 DuckDB, 不调 adapter, 不写库 (api 用)
- `get_bars_fresh`: adapter 拉 → 写库 → 写 cache → 发 bus 事件 (collector 用)

**改造点 4**: Stampede 防护
- `state:inflight:bars:{symbol}:{interval}` 短 TTL 锁
- 高并发同 symbol 只 1 个真触发 refill

**收益**: 重复访问 100% 命中 Redis < 10ms

### 3.5 症状 4: 分时数据 500 报错

**根因**: `stock_zh_a_minute` sina IndexError, api 路由直接抛.

**改造**:
- api 路由不再 ak_call → 从根上消除 500
- collector 拉失败结构化记 source_status, N 次后切 outlet
- API 总返回 200, 缺数据用 `meta.stale + meta.reason`

**收益**: 500 → 0

### 3.6 前端配合改动 (Stage 6 范围)

| 改动 | 文件 | 工作量 |
|---|---|---|
| 解析 `meta.stale` / `meta.partial` 染灰对应卡片 | 通用组件 + 各页面 | 中 |
| 调 `/api/markets/{m}/dashboard` 替代多接口 | `apps/web/app/market/page.tsx` | 小 |

**SSE 替换轮询推迟到下一个 Plan**.

### 3.7 路由清单逐项审计

实施期出一张表, 列所有 `apps/api/routes/*.py` 路由, 标注现状 (是否 ak_call) + 改造后 (数据源 / 是否需要 refill_request). 确保 0 路由违反 3.1.

### 3.8 关键边界

Redis 是前置缓存, 不是 SSoT. 任何时候 Redis 挂或冷启动, api 都能从 DuckDB/SQLite 拿到 "上次落库快照". **这是设计的关键安全网**.

---

## 4. 写路径 / 采集层 (Outlet + Leader + 限流熔断)

### 4.1 collector 进程结构

```
apps/collector/main.py
  ├─ leader.py            ── 单例守门: 抢 Redis 锁, 只 leader 让 scheduler 跑
  ├─ scheduler.py         ── APScheduler, 所有 cron 在此
  ├─ jobs/
  │   ├─ tick_quote.py        ── 各市场 tick 快照
  │   ├─ flush_bars.py        ── QuoteCache → DuckDB 1m bars
  │   ├─ index_minute.py      ── 8 大指数 5min 序列 (新)
  │   ├─ market_dashboard.py  ── 大盘聚合包 → cache (新)
  │   ├─ fetch_intraday.py    ── 5m/15m/30m
  │   ├─ aggregate_intraday.py── 60m/4h
  │   ├─ fund_flow.py         ── 北向 + 个股
  │   ├─ scan_signals.py      ── CD 信号
  │   ├─ refill_consumer.py   ── 订阅 bus:bars.refill_request (新)
  │   └─ chip_summary.py      ── 筹码摘要 (改: 日终预热)
  └─ http.py              ── /health 暴露给运维, 不暴露业务接口
```

**边界**:
- 任何 job 失败必须在 job 内 catch (沿用现有规范 §5)
- 任何 job 完成必须写两处: DB (SSoT) + Redis cache + 发 bus 事件
- 任何 ak_call 必须经 outlet & ratelimit & breaker 三层

### 4.2 Leader 选举 (单机也走完整流程)

```python
class Leader:
    KEY = "state:leader:collector"
    TTL_S = 15
    RENEW_S = 5

    async def acquire_loop(self):
        while True:
            ok = await redis.set(self.KEY, self.node_id, nx=True, ex=self.TTL_S)
            if ok or await redis.get(self.KEY) == self.node_id:
                await redis.expire(self.KEY, self.TTL_S)
                self._is_leader = True
            else:
                self._is_leader = False
            await asyncio.sleep(self.RENEW_S)
```

scheduler 每个 cron trigger 入口检查 `leader.is_leader()`, 不是就 return.

**单机**: 1 个 collector 永远抢到锁, 行为等价直跑, 但代码已 "多节点 ready".
**未来多节点**: leader 崩 (15s 未续期) → 锁过期 → 备节点抢. RTO ≤ 15s.

### 4.3 ak_call 三层中间件

每个 ak_call 必须穿过:
```
                       ┌─ check breaker (this source 是否熔断)
ak_call(name, caller) ─┼─ check ratelimit (令牌桶, 无 token 阻塞)
                       └─ acquire outlet (从池里拿 1 个, 注入子进程 env)
                          │
                          ▼
                       run_subprocess(ak_func, env=outlet.env)
                          │
                          ▼
                       evaluate_response(df) → outcome
                          │
                          ▼
                       report 给 breaker, outlet, ratelimit
```

#### 4.3.1 Breaker 层 — pybreaker

- per-source (sina / em / ths / ak_default), 不是 per-caller
- 状态: closed → open → half-open → closed
- 阈值: 近 60s 失败率 ≥ 60% 且样本 ≥ 5 → open
- open 持续 5min → half-open (放 1 个探针) → 探针成功回 closed
- 状态变化写 `state:source:{name}` + 发 `bus:source.status`

#### 4.3.2 Ratelimit 层 — 纯 Lua + Redis 令牌桶

- per-source 配置:
  - sina: 5 tok/s, burst 20
  - em: 10 tok/s, burst 50
  - ths: 3 tok/s, burst 10
- Lua 脚本 `EVAL` 一次原子拿 token
- 没拿到: 等 (blocking) 而非抛, 让 ak_call 自然排队

#### 4.3.3 Outlet 层 — 出口管理

```python
class Outlet(Protocol):
    name: str
    async def acquire(self) -> OutletLease: ...
    async def report(self, lease, outcome): ...

class LocalOutlet:
    def acquire(): return OutletLease(env={})

# 未来 (Plan 之外)
class KuaidailiOutlet:
    def acquire(): return OutletLease(env={"HTTPS_PROXY": "http://user:pass@..."})
```

`OutletPool` 在 Redis 维护 `state:outlet:*`, acquire 时按权重 + 健康度选 1 个, 被 ban 后 cooling N 分钟.

#### 4.3.4 响应评估器

```python
def evaluate_response(df, source) -> Outcome:
    if df is None or df.empty:
        return Outcome.empty
    if source == "sina" and not _looks_like_real_quote_df(df):
        return Outcome.banned
    return Outcome.ok
```

"成功返回但内容异常" 也算失败, 是 IP ban 早期信号.

### 4.4 多 source fallback (只挑 5~10 个关键 caller)

```python
await ak_call_with_fallback(
    intent="ashare.index.minute_5m",
    sources=[
        ("stock_zh_a_minute", {"symbol": "sh000001", "period": "5"}),  # primary: sina
        ("index_zh_a_hist", {"symbol": "000001", "period": "daily"}),  # fallback: em
    ],
    caller="...",
)
```

挑选清单:
- 指数 minute (sina → em)
- 全 A 快照 (em → sina 备)
- 板块成分 (em → ths)
- 个股日线 (sina → em)

其余 caller 单 source, 失败就 stale.

### 4.5 子进程 env 注入

```python
def _spawn_subprocess(ak_func_name, args, kwargs, *, env_extras: dict):
    env = {**os.environ, **env_extras}  # outlet 注入 HTTP_PROXY
    return subprocess.run([...], env=env)
```

子进程结束 V8 状态自然销毁, 雷区 1 永久消除 (已实现). outlet env 注入只是已有路径的扩展.

### 4.6 cron 频率审计

| Job | 现频率 | 建议 | 理由 |
|---|---|---|---|
| tick_quote | 10s | 保持 | quote 核心 |
| flush_bars | 60s | 保持 | 1m bar 入库 |
| index_minute (新) | (路由触发) | **每 30s** | 替代路由内 ak_call |
| market_dashboard (新) | 无 | **每 60s** | 大盘聚合 |
| fetch:ashare:5m | 每 15min | 保持 | 5m bar |
| ff:north | 每 1min | **每 2min** | 北向变化不需要 60s 粒度 |
| ff:symbols | 每 30min | 保持 | 个股资金流 |
| cd:* | 各 cron | 保持 | 已收盘后触发 |
| chip_summary | 用户访问触发 | **日终 15:35 全量预热** | 收盘后一次性算 |

**净下降**: 总 ak_call 频率下降 30~50%, 完全可预测.

### 4.7 故障矩阵 (采集层)

| 故障 | 检测 | 处理 |
|---|---|---|
| 单 symbol 拉失败 | adapter raise | warning, 跳过, batch 继续 |
| 单 source 限流 | breaker 检测 | open 5min → 探针恢复 |
| 单 source IP ban | breaker + outlet.banned | 切 outlet → 该 source cooling 30min |
| 所有 outlet 都 ban | OutletPool 全员 banned | 该 source 全停 5min, bus 推 status, api 染灰 |
| akshare 全网挂 | 全 source banned | 全停采集, api 用 DB 快照 |
| collector 进程崩 | systemd | 自动重启, Redis 锁过期后接管 |
| Redis 挂 | client 异常 | breaker 内存态降级, collector 继续 ak_call, api fallback DB |

### 4.8 迁移路径概览

详细 6 阶段见第 6 节. 每阶段独立可验证, 独立部署.

---

## 5. 故障矩阵全景 + 可观测性 + 测试

### 5.1 端到端故障矩阵

| # | 故障源 | 用户/系统感知 | 系统处理 |
|---|---|---|---|
| 1 | 单 ak_call raise | 单条数据缺 | warning, 跳过, batch 继续 |
| 2 | 单 source 限流 | parse_error 增多 | breaker open 5min → 探针 |
| 3 | 本机出口 IP ban | source 全部失败但 outlet 可达 | outlet 切下一个 → cooling 30min |
| 4 | akshare 全网挂 | 所有 source banned | 停采集 5min, api 退 DB 快照, 前端染灰 |
| 5 | collector 进程崩 | 没人写 cache, TTL 过期空 | systemd 重启, Redis 锁过期接管, api DB fallback |
| 6 | collector 卡住 | 锁未续期 | 15s 后锁过期 → 重启 / 备节点接管 |
| 7 | Redis 挂 | api 读 cache 失败 | api 自动 fallback DB |
| 8 | DuckDB 损坏 | api fallback 也失败 | api 503, collector 重启尝试 repair |
| 9 | SQLite 锁冲突 | 信号写失败 | WAL 模式极少, retry + warning |
| 10 | bus 堆积 | api SSE 推迟 | MAXLEN 裁剪, 丢老消息 |
| 11 | api 进程崩 | 请求 502 | systemd 重启, 前端 reconnect |
| 12 | 前端长时间无新数据 | stale 标记不变 | data_age 超阈值 → 红提示 + 触发 refill |
| 13 | 配置错误 | collector 启动失败 | fail-fast, systemd loop, 日志明示缺啥 |
| 14 | mini_racer SIGABRT (雷区 1) | 子进程崩 | 子进程隔离, 主进程不受影响 (已实现) |

**关键**: 故障 5/6/7 都是常见运维场景, 任意单点 api 不会 502.

### 5.2 监控指标 (Prometheus 格式, 单机暂不部署 Prom)

`/metrics` 端点, collector 与 api 各一份. 指标先埋, Prometheus 抓不抓后续再说.

**Collector 关键**:
```
# Counter
ak_call_total{source, intent, outcome}
ak_call_duration_seconds{source}
breaker_state_changes_total{source, to_state}
outlet_acquire_total{outlet, outcome}
ratelimit_blocked_seconds_total{source}
job_runs_total{job, outcome}
job_duration_seconds{job}
bus_publish_total{topic}
cache_write_total{scope}

# Gauge
leader_is_active                            0/1
breaker_state{source}                       0=closed, 1=open, 2=half_open
last_successful_tick_ts{market}
source_health{source}                       0=ok, 1=degraded, 2=banned
```

**API 关键**:
```
http_requests_total{route, status}
http_request_duration_seconds{route}
cache_hits_total{scope}
cache_misses_total{scope}
db_fallback_total{route}                    应为 0
sse_clients{topic}                          后续 Plan
refill_request_published_total{intent}
stale_response_total{route}
```

### 5.3 结构化日志 (沿用项目规范 §6)

```
collector.boot / collector.shutdown
leader.acquired / leader.lost
job.start / job.end
ak_call.invoke / ak_call.outcome
breaker.opened / breaker.closed
outlet.banned / outlet.recovered
bus.published

# api
http.request
cache.hit / cache.miss
db_fallback.activated
```

### 5.4 测试策略

#### 5.4.1 单元测试 (`tests/unit/`)

| 模块 | 重点 |
|---|---|
| Outlet / OutletPool | 选择算法, banned 后状态机, cooling 解除 |
| Breaker | 阈值触发, half-open 探针, 状态切换计数 |
| Ratelimit | 令牌桶算法 (fakeredis + mock time) |
| RedisCache | msgpack 序列化, TTL 设置 |
| Leader | acquire / renew / lost 三场景 (fakeredis) |
| KLineService.get_bars_cache_only | 不调 adapter, 覆盖 vs 完整独立判断 |
| evaluate_response | sina banned 伪正常返回检测 |

#### 5.4.2 集成测试 (`tests/integration/`)

`pytest-redis` fixture 自动起 + 销毁本机 Redis.

| 场景 | 期望 |
|---|---|
| collector tick → api 读 cache | 读路径完整闭环 |
| collector 崩 (SIGTERM) → api DB fallback | 故障 7 验证 |
| 模拟 source banned → api stale meta | 故障 3 验证 |
| Redis 关 → api 服务 | 故障 7 验证 |
| 多 source fallback | primary fail → secondary 上 |
| bus:bars.refill_request → collector 拉回写 | 按需补全闭环 |

#### 5.4.3 故障注入 (`scripts/chaos/`, 不入 CI)

- `kill_collector.sh` — 观察 api 行为
- `block_redis.sh` — `iptables` 阻断 6379
- `simulate_ban.py` — ak_call 全返伪正常
- `flood_quote.py` — 高并发同 symbol

### 5.5 性能验收基准 (`tests/perf/bench.py`)

| 路由 | p50 | p95 | p99 |
|---|---|---|---|
| `/api/symbols/{s}/quote` | < 5ms | < 20ms | < 50ms |
| `/api/symbols/{s}/bars` (cache hit) | < 30ms | < 80ms | < 150ms |
| `/api/symbols/{s}/bars` (DB hit) | < 50ms | < 150ms | < 300ms |
| `/api/indices/{s}/minute` (cache hit) | < 10ms | < 30ms | < 80ms |
| `/api/markets/ashare/dashboard` | < 50ms | < 150ms | < 300ms |
| `/api/cd-signals` | < 30ms | < 80ms | < 200ms |

**当前基线对照**:
- `indices/minute` p95 ~5-15s (抢锁)
- `bars` p95 ~200ms-5s (cache miss 时)

**改造目标**: 全部 p95 < 300ms, p99 < 500ms.

---

## 6. 落地阶段路径 + 不在本次范围 + 验收

### 6.1 Stage 1 — Redis 基建 + 客户端封装

**目标**: Redis 跑起来, api/collector 能连, 业务零变化.

| 工作 | 交付 |
|---|---|
| `make dev` 加 Redis (docker-compose) | `docker-compose.dev.yml`, `Procfile` |
| `core/cache/redis_client.py` (连接池, msgpack 编解码, key 校验装饰器) | 新 |
| `core/cache/keys.py` (常量 + 构造函数集中) | 新 |
| `tests/unit/cache/test_redis_client.py` | 单测 |
| api 启动连 Redis, 失败仅 warning 不阻塞 | 改 `apps/api/main.py` |
| 现有路由不动 | — |

**验证**: `make dev` 跑通, Redis ping 通, api 启动 log `redis.connected`.
**风险**: 0.

### 6.2 Stage 2 — collector 进程拆分

**目标**: scheduler 从 api 进程搬到独立 collector.

| 工作 | 交付 |
|---|---|
| `apps/collector/main.py` asyncio entrypoint | 新 |
| `apps/api/scheduler.py` 注册逻辑挪到 `apps/collector/scheduler.py` | 重构 |
| `apps/api/main.py` 移除 scheduler 代码 | 改 |
| `Procfile` 加 collector 进程 | 改 |
| collector `/health` 暴露 (8788), 含 leader 状态 + last tick ts | 新增 |
| `make dev` / `make test` 同步 | 改 |

**验证**:
- `make dev` 跑起来, collector 进程在, scheduler 在 collector 跑不在 api 跑
- 现有所有功能行为不变
- api 重启 scheduler 不受影响

**风险**: 中. `pkill -9 uvicorn` 后忘记重启 collector 是新坑 — **CLAUDE.md 雷区 2 重启模板要更新**.

### 6.3 Stage 3 — Leader + Outlet + Breaker + Ratelimit

**目标**: ak_call 三层中间件落地, 单机行为等价现状但代码 ready.

| 工作 | 交付 |
|---|---|
| `apps/collector/leader.py` + acquire_loop | 新 |
| `core/integrations/outlets/{base,local}.py` | 新 |
| `core/integrations/breaker.py` (pybreaker, 状态写 Redis) | 新 |
| `core/integrations/ratelimit.py` (Lua 令牌桶) | 新 |
| `core/integrations/akshare.py::ak_call` 穿三层 | 改 |
| `core/integrations/_ak_runner.py` 接受 env_extras | 改 |
| `evaluate_response()` 加 source-specific banned 检测 | 改 |
| 单元测试 | `tests/unit/integrations/` |

**验证**:
- 现有 ak_call 仍成功 (三层默认放行)
- 模拟 sina banned signature → breaker open
- `state:source:sina` 在 Redis 能看到状态
- `bus:source.status` 有事件

**风险**: 高 (动 ak_call 最敏感路径). 严密观察 `data/logs/api-errors.log`, 异常立即回滚.

### 6.4 Stage 4 — collector 新增 job + 预聚合

**目标**: 前端要的数据预先备好.

| 工作 | 交付 |
|---|---|
| `jobs/index_minute.py` (8 指数 5min, 30s 一次) | 新 job |
| `jobs/market_dashboard.py` (大盘聚合, 60s) | 新 job |
| `jobs/refill_consumer.py` (订阅 bus:bars.refill_request) | 新 job |
| `chip_summary` 改为日终预热 (15:35 全 watchlist) | 改 |
| 所有 job 完成写 cache + 发 bus | 改 |
| cron 频率审计 (ff:north 1min → 2min) | 改 |

**验证**:
- Redis 查到 `cache:index:000001.SH:minute`, `cache:market:ashare:dashboard`
- bus 流有 `bus:bars.updated`
- collector log 显示 job 按预期周期跑

**风险**: 低 (增量).

### 6.5 Stage 5 — api 切到 cache, 删除 ak_call 依赖

**目标**: **4 个症状全部消除**.

| 工作 | 交付 |
|---|---|
| `apps/api/routes/indices.py` 改读 `cache:index:*`, 删 ak_call import | 改 |
| `routes/symbols.py:bars` 加 Redis 前置缓存 + stampede 防护 | 改 |
| `KLineService.get_bars_cache_only` / `get_bars_fresh` 双轨 | 改 |
| `_missing_ashare_daily_metrics` 改 partial 标记不触发回填 | 改 |
| `/api/markets/{m}/dashboard` 新增 | 新增 |
| API DTO 全加 `meta` 字段 (stale/partial/data_age_seconds) | 改 |
| `tests/integration/test_read_path.py` 端到端 | 新测 |
| 审计: `grep -rn ak_call apps/api/` 必须为空 | 验证步骤 |

**验证**:
- 所有路由 p95 < 300ms
- 模拟 collector 关停 → api 返 stale meta
- 模拟 Redis 关停 → api DB fallback

**风险**: 中. 前端先兼容旧 DTO, api 加 meta 非破坏性.

### 6.6 Stage 6 — 前端 stale 染灰 (SSE 推迟)

**目标**: 用户看见诚实状态.

| 工作 | 交付 |
|---|---|
| `apps/web/components/StaleBadge.tsx` 通用组件 | 新增 |
| 各页面读 `meta.stale` / `meta.partial` 染灰 | 改 |
| `/market` 改用 dashboard 聚合接口 | 改 |

**SSE 替换轮询**推迟到下一个 Plan (单独立项).

**验证**:
- 前端可见 "数据延迟 N 秒" 提示
- 关 collector 后 UI 染灰不报错

**风险**: 低 (前端独立).

### 6.7 不在本次范围 (YAGNI)

| 项目 | 为什么不做 | 何时考虑 |
|---|---|---|
| 商业代理池接入 (快代理/阿布云) | 用户暂未要 | 实际遭遇 IP ban, Outlet 已 ready, 加子类 |
| 多节点 collector 部署 | 单机够 | 需要跨机房高可用, Leader 已 ready |
| Prometheus + Grafana | 第 0 章原则 | 指标先埋, 后续对接 |
| Playwright E2E | V1 已声明 | 后续 Plan |
| 主备热备代码层 | 用 Leader 顶替 | 同上 |
| Redis Cluster / 持久化 | 单机不需要 | 需要持久化时打开 RDB |
| DuckDB 升级 PG | 单机文件够 | 多 api 节点共享时再上 |
| 1m bar 入库 | 维持现状 | 真要持久化历史 1m 时 |
| SSE 替换轮询 | 本次只做 stale 染灰 | 下一个 Plan |
| Tracing (OTel) | 单机日志能定位 | 跨服务时 |

### 6.8 验收清单 (整个 spec 完工标准)

**功能验收**:
- [ ] `make dev` 一键起 redis + collector + api + web
- [ ] `grep -rn "from core.integrations.akshare import ak_call" apps/api/routes/` 为空
- [ ] `grep -rn "import akshare" core apps tests` 仅命中 `core/integrations/akshare.py`
- [ ] 所有路由响应不含 mini_racer 锁等待
- [ ] 关停 collector 60s, api 仍 200 OK (stale meta)
- [ ] 关停 Redis, api 仍 200 OK (DB fallback)

**稳定性验收**:
- [ ] 连续 24h 跑测, api 进程零 500 错误
- [ ] 连续 24h 跑测, collector 进程零 SIGABRT
- [ ] `data/logs/api-errors.log` 24h ERROR < 100 条

**性能验收**:
- [ ] `tests/perf/bench.py` 全部路由 p95 < 300ms, p99 < 500ms
- [ ] `/api/markets/ashare/dashboard` p95 < 150ms
- [ ] 重复访问同 symbol K 线, 第 2 次起 < 30ms

**可观测性验收**:
- [ ] `/metrics` 输出 Prometheus 格式, 关键指标 11+ 项
- [ ] `state:source:*` / `state:outlet:*` 在 banned 时状态变化可见
- [ ] `bus:source.status` 状态切换时有事件

**已知不验收**:
- IP ban 真实场景 (无商业代理无法构造完整测试)
- 多节点 leader 切换 (单机部署, 仅单元测试)

---

## 7. 文档与参考

### 7.1 现有项目文档

- `CLAUDE.md` 5 个雷区 (本 spec 兼容并扩展)
- `docs/superpowers/specs/2026-05-13-marketpulse-design.md` 整体架构
- `docs/TODO.md` 优化清单 (本 spec 完成后该清单部分项可划掉)

### 7.2 外部依赖新增

- `redis>=5.0` (Python 客户端, 5.x 支持 Streams + asyncio)
- `pybreaker>=1.0` (熔断器)
- `ormsgpack>=1.4` (msgpack 序列化)
- `honcho>=1.1` (dev 进程管理)
- `pytest-redis>=3.0` (测试)
- Redis server (通过 docker-compose, 镜像 `redis:7-alpine`)

### 7.3 不引入

- NATS / Kafka / RabbitMQ (用 Redis Streams 替代)
- redis-cell module (用纯 Lua 替代)
- Prometheus / Grafana (指标先埋)
- WebSocket (用 SSE 替代, 推迟)
- Celery / Dramatiq (用 APScheduler + bus 替代)

---

## 8. 公开问题 (实施前最终确认)

均已在 brainstorming 期间拍板:
- Q1 Redis 部署: **docker-compose**
- Q2 限速器: **纯 Lua + Redis 令牌桶**
- Q3 序列化: **缓存 msgpack, bus JSON**
- Q4 推送: **SSE (本次推迟实现)**
- Q5 dev 启动: **honcho + Procfile**
- Q6 前端范围: **本次只做 stale meta 染灰, SSE 推迟**

---

## 9. 下一步

1. **用户 review 本 spec**
2. 调用 `writing-plans` skill 产出 6 阶段实施计划
3. 实施计划逐阶段 review 后用 `executing-plans` 执行

