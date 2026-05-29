# Crypto 接入 + SSE 推送 + Collector 进程拆分

**日期**: 2026-05-29
**作者**: zhonghuai + Claude
**状态**: 待实施

---

## 0. 背景

目前 collector 是单进程,所有市场(A 股 / 美股 / crypto)cron 跑在同一 event loop。问题:

1. **故障域过大**:任一 adapter 异常都可能拖累全局(已踩:V8 race / Alpaca SSL 断 / 子进程 hang)
2. **资源争抢**:ak_call 长 timeout 排队,挤占 Binance WS 心跳
3. **DuckDB 跨进程并发死锁**:api(RO)与 collector(RW)在 macOS 上文件锁互斥(已踩,2026-05-29 用 Redis tail 绕开,但 collector 内部仍单文件)

同时缺失能力:
- crypto 实时数据(coingecko 限频已废,需切 Binance)
- 实时 push(目前 SWR pull 在交易时段每 30s 轮询)
- crypto 全周期 K 线展示

---

## 1. 目标

### 1.1 拆 collector 进程
collector 按市场拆 3 个独立进程,故障隔离:
- `collector_ashare` (端口 8788)
- `collector_us`     (端口 8789)
- `collector_crypto` (端口 8790)

各进程:独立 event loop / 独立 adapter / 独立 cron / 独立 DuckDB 文件。

### 1.2 接入 crypto
- 5 标的:**BTC / ETH / SOL / XRP / TRX**(USDT 计价)
- 全周期:`5m / 15m / 30m / 60m / 4h / 1d / 1wk / 1mo`
- Binance Spot REST(首次 backfill,能拉多少拉多少)+ WS(增量 push)

### 1.3 SSE 推送
新增 `/api/sse/bars/{symbol}/{interval}` 路由,前端 EventSource 增量更新。
本期只 crypto 接入,A 股 / 美股仍走 SWR pull(P7 后续扩展)。

### 1.4 不在范围
- A 股 / 美股已知 K 线 bug 不动
- 1m 路径不动(进程内 55s 短缓存)
- intraday 概念退化不做(降低风险)
- A 股 / 美股 SSE 接入不做

---

## 2. 决策

### 2.1 DuckDB 按 market 分文件

| 选项 | 评估 |
|---|---|
| 单文件 + 重试 | macOS 跨进程文件锁恶心,已踩 |
| **3 文件独立** ✅ | 文件锁完全隔离,3 进程并行写无冲突 |

新路径:
- `data/bars_ashare.duckdb`
- `data/bars_us.duckdb`
- `data/bars_crypto.duckdb`

`BarRepo(db_path)` 由 collector 进程显式构造对应路径。api 进程不再持 DuckDB(已经在 2026-05-29 commit 改造完)。

### 2.2 旧数据迁移策略 C(慢周期保留,intraday 抛弃)

迁移脚本一次性跑:
- 从 `data/bars.duckdb` 按 market 拆出 `1d / 1wk / 1mo` → `bars_{market}.duckdb`
- `5m / 15m / 30m / 60m / 4h / 1m` 全部抛弃,等 cron 自动重新拉

理由:intraday 数据每天 cron 在拉,丢了无所谓;1d 是 sina/Alpaca 历史数据要重新分页拉很慢,保留。

### 2.3 Leader 锁简化为 no-op

3 进程拆开后,A 股 cron 只在 `collector_ashare` 跑 → 自己就是唯一 leader。`_leader_gated` 包装变成 pass-through(留 hook 给未来多副本)。`Leader` / `acquire_loop` 类暂保留代码,实际不再在 lifespan 启动。

### 2.4 共享代码 vs 进程入口

- `core/` 不变(共享 adapter / service / scheduler)
- `apps/collector/` 拆:
  ```
  apps/collector/
    base.py             # 通用 lifespan helper (proxy + logging + middleware setup + health)
    ashare/main.py      # A 股进程入口,装 attach_signal_jobs + ff:* + chip + 自家 tick:ashare
    us/main.py          # 美股进程入口,装 attach_us_signal_jobs + 自家 tick:us
    crypto/main.py      # 新增,装 binance_ws_consumer task + backfill job
  ```
- 每个 main 起一个 FastAPI(仅 `/health`)用于 honcho 探活

### 2.5 Crypto 数据源

| 用途 | 接口 | 备注 |
|---|---|---|
| 历史 K 线 | `GET /api/v3/klines?symbol=BTCUSDT&interval=5m&limit=1000` | 免费无 token,limit ≤ 1000,分页 endTime 回溯 |
| 实时增量 | WS `wss://stream.binance.com:9443/stream?streams=btcusdt@kline_5m/...` | combined stream,40 路 1 connection |
| latest snapshot | `GET /api/v3/ticker/24hr` | 给 quote 用 |

interval 映射(adapter 层处理):
| 项目 | Binance |
|---|---|
| 5m / 15m / 30m / 60m / 4h | 5m / 15m / 30m / 1h / 4h |
| 1d / 1wk / 1mo | 1d / 1w / 1M |

symbol 映射:`BTC-USDT` (项目)↔ `BTCUSDT` (Binance)

### 2.6 SSE 协议

```
GET /api/sse/bars/BTC-USDT/5m

response:
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

event: init
data: {"bars":[<最末 200 根>],"server_ts":"..."}

event: bar
data: {"ts":"...","open":...,"high":...,"low":...,"close":...,"volume":...}

(每 30s)
event: ping
data: {"server_ts":"..."}
```

实现:server 端 `xreadgroup` 阻塞读 `bus:bars.updated`,filter 掉非本 (symbol, interval) 的消息。心跳 30s 保活。断连后 EventSource 自动重连,新连接重发 init(全量 reconcile)。

---

## 3. 架构

```
┌──────────────────────────────────────────────────────────────────┐
│ 4 个 long-running 进程 (honcho 拉起)                             │
│                                                                  │
│  collector_ashare      collector_us         collector_crypto     │
│      :8788                :8789                  :8790           │
│      ↓ ak_call            ↓ Alpaca SDK           ↓ httpx + ws    │
│      ↓ writes             ↓ writes               ↓ writes        │
│  bars_ashare.duckdb   bars_us.duckdb         bars_crypto.duckdb  │
│      │                    │                      │               │
│      └────────────────────┴──────────────────────┘               │
│                            │                                     │
│  ┌──────────────────────────────────────────────────────┐        │
│  │  Redis (cache:bars:* / bus:bars.updated / state)     │        │
│  └──────────────────────────────────────────────────────┘        │
│                            ▲                                     │
│                            │ reads only                          │
│                       ┌────────┐                                 │
│                       │  api   │ :8787 (SSE + REST)              │
│                       └────────┘                                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 实施切分(7 个 plan)

| Plan | 内容 | 验收 |
|---|---|---|
| **P1** | collector 拆 3 进程 + DuckDB 按 market 分文件 + 迁移脚本 + Procfile/Makefile 调整 | 3 进程独立起,A 股 / 美股 K 线照常,API 健康检查 200 |
| **P2** | BinanceAdapter REST(klines + ticker)+ crypto backfill cron | `curl /api/symbols/BTC-USDT/bars?interval=5m` 返回历史 |
| **P3** | binance_ws_consumer + xadd `bus:bars.updated` | `grep "ws.kline_closed"` 看到周期性事件 |
| **P4** | api `/api/sse/bars/{symbol}/{interval}` 路由 | `curl -N` 拿到 init + bar event |
| **P5** | 前端 `useKlineStream` hook + crypto 详情页接入 | 浏览器 BTC 详情页 5 分钟看到新 K |
| **P6** (本次范围外) | A 股 / 美股 K 线 bug 修复(refill 时序、`_covers` 等) | 详情页所有 interval 切换都有数据 |
| **P7** (本次范围外) | A 股 / 美股 也接 SSE,session 外自动 close | 美股 ET 16:00 收盘后 SSE close |

本次实施 P1-P5。

---

## 5. 关键模块

### 5.1 新建
- `core/adapters/binance.py::BinanceAdapter` — REST + WS 数据接入
- `apps/collector/crypto/main.py` + `apps/collector/crypto/ws_consumer.py`
- `apps/collector/ashare/main.py`(从旧 main.py 拆出)
- `apps/collector/us/main.py`(同上)
- `apps/collector/base.py` — 通用 lifespan(proxy + logging + middleware setup)
- `apps/api/routes/sse_bars.py` — SSE 路由
- `apps/web/lib/use_kline_stream.ts` — hook
- 一次性迁移脚本 `scripts/migrate_bars_per_market.py`

### 5.2 修改
- `core/services/kline_service.py::_persist_bars`:写完 xadd `bus:bars.updated`
- `core/cache/keys.py`:`BUS_BARS_UPDATED` schema 文档化为 `{market, symbol, interval, ts, ohlcv}`
- `apps/api/deps.py`:删除 `get_bar_repo()`(api 不再持 DuckDB,完全脱离)
- `apps/web/app/symbol/[code]/page.tsx`:crypto market 用 `useKlineStream`,其他仍 SWR
- `Procfile` / `Makefile`:加 collector_us / collector_crypto entry

### 5.3 不动
- A 股 / 美股 adapter 实现
- `aggregate_intraday`(crypto 不需要,WS 直接推原生 interval)
- `_INTRADAY_RAW` / `_INTRADAY_AGG` 等遗留分类

---

## 6. 时序

### 6.1 collector_crypto 启动
1. lifespan:
   - setup proxy + logging(`process_name="collector_crypto"`)
   - 注入 ak_middleware(crypto 不用,但保持一致)
   - 启动 `binance_ws_consumer` task(asyncio create_task)
   - 启动 `crypto_backfill_job`(一次性跑,APScheduler one-shot)
2. backfill job:对 5 标的 × 8 周期 → 分页拉历史到尽头,写 DuckDB + Redis tail
3. WS consumer 持续接管增量,每根收盘后写 DuckDB + Redis tail + xadd `bus:bars.updated`

### 6.2 用户开 BTC 详情页
1. SWR 拉 `/api/symbols/BTC-USDT/bars?interval=5m&days=5`(走 Redis tail,即时返回)
2. 同时 EventSource 连 `/api/sse/bars/BTC-USDT/5m`
3. 收到 `init` event(server 推最末 N 根)→ reconcile 历史
4. 收到 `bar` event(每 5 分钟一次)→ append/replace 最末根

### 6.3 SSE 路由内部
1. 客户端连接 → server `RedisCache._r.xreadgroup(BUS_BARS_UPDATED, group, ">")` 阻塞读
2. 收到消息 → 解码 → filter `(symbol, interval)` 命中 → yield SSE event
3. 30s 无消息 → yield `ping` event
4. 客户端断开 → server 协程被 cancel → ack 当前消息后退出

---

## 7. 测试与验收

### 7.1 单测
- `BinanceAdapter._parse_kline` (closeTime+1ms 转 UTC,值正确)
- `BinanceAdapter.fetch_history` 分页(mock httpx,触发 endTime 回溯)
- `binance_ws_consumer.handle_message`:`k.x=true` 写,`x=false` 跳过
- `sse_bars` 路由(mock Redis,断言 init + bar + ping event 序列)
- `kline_service._persist_bars` 写完触发 xadd(mock RedisCache)

### 7.2 集成(标 `@pytest.mark.integration`)
- 真连 Binance,拉 BTC 5m 1 根 + WS 5s 内收到 push

### 7.3 验收
- 5 标的全周期 K 线在前端可见
- 任意 interval 切换均有数据(不依赖 24h cron)
- crypto 详情页打开 5 分钟内看到一根新 K 收盘
- 杀 `collector_ashare` 进程,`collector_crypto` / `collector_us` 不受影响,继续写库 / 推送
- api 健康检查 200,K 线接口 200

---

## 8. 风险

1. **Binance REST 限流**:1200 weight/min/IP,backfill 加 100ms 间隔 + 失败指数退避
2. **WS 单 connection 1024 streams 上限**:5×8=40 路远低于上限
3. **Redis Streams 堆积**:`bus:bars.updated` MAXLEN ~10000(approximate),老消息自然丢
4. **DuckDB 文件锁**:3 进程各自独立文件 → 完全无冲突
5. **SSE 断连重发 init 全量**:浏览器 EventSource 默认 3s 重试,首次重试拿 init 重 reconcile
6. **历史迁移损坏**:迁移脚本前先 `cp bars.duckdb bars.duckdb.before-split-{date}` 备份
7. **honcho 进程顺序依赖**:redis 先起,3 个 collector + api 都依赖 redis 健康

---

## 9. 历史

- 2026-05-13:原始 spec,collector 单进程
- 2026-05-27:拆出 collector + api(2 进程),Plan 1
- 2026-05-28:加固日志 / cron 闸门 / Redis bars tail
- 2026-05-29:发现 DuckDB 跨进程锁问题,Redis tail 改造
- 2026-05-29(本 spec):3 collector 进程隔离 + crypto + SSE
