# MarketPulse 多用户 / VPS 扩展性评审(目标 ~100 并发)

> 当前架构按"单机 + 单人本地工具"设计(CLAUDE.md 第 0 章原则 4/5)。本文评估迁到公网 VPS、~100 并发用户时的瓶颈、撞墙点,并给出分优先级的多用户化方案。

- **日期**:2026-06-02
- **评审人**:zhonghuai + Claude
- **范围**:读路径(api / SSE / 轮询)+ 部署拓扑 + 数据源外部限制。**采集侧不在此列**(见 §1 结论:采集是用户无关的,不随用户数变化)。
- **配套**:数据完整性/采集审计见 `docs/2026-06-01-collection-db-backfill-audit.md`。

---

## 0. 结论速览(TL;DR)

- **采集侧零压力**:三 collector 单实例(leader 锁),不管 1 人还是 1000 人看,采集逻辑/频率/入库完全一样。**只有"读 + 实时下发"侧需要重做。** 这把改造范围收敛到 api/SSE。
- **3 堵硬墙(直接放公网就撞)**:
  1. **Alpaca 免费 IEX `trades` 30 符号上限** —— 100 人看 >30 只不同美股时,超出的拿不到实时。
  2. **refill 放大** —— 公网用户点冷门标的触发后端 `ak_call`/Alpaca,100 人能把数据源打爆/封 IP。
  3. **零鉴权/零限流** —— 公网裸奔,易被刷爆。
- **架构性瓶颈(100 人吃紧、几百人塌)**:
  4. **SSE 全局流 + 每连接各自过滤** —— CPU 开销 = O(用户数 × 全局消息速率),全压在单核。
  5. **单 uvicorn worker** —— 1 进程 1 核扛所有读 + SSE 扇出。
  6. **每条 SSE 占一条 Redis 阻塞连接** —— 200 连接 ≈ 200 Redis 连接,连接池/fd 需调。
  7. **历史 K 线读耦合到 collector 单写进程** —— 100 人翻页在 collector 锁里排队,和采集抢进程。
- **运营短板**:Next.js 跑在 **dev 模式**(非生产构建)、单 Redis 单点、无反代/HTTP2、无可观测。

**判断**:这不是小修,是**读/实时下发侧的一次专门多用户化**。但因采集侧不动,范围可控。下面 §5 给分优先级路线图。

---

## 1. 部署拓扑现状(实测)

```
make dev → honcho 拉起(Procfile):
  collector_ashare / collector_us / collector_crypto   (各单进程, leader 锁)
  api:  uvicorn apps.api.main:app --port 8787           ← 无 --workers = 单 worker 单核
  web:  next dev                                        ← dev 模式, 非 next build/start
  redis: docker-compose 单实例                           ← 单点
无 nginx/caddy 反代; 无鉴权; 无限流网关; 无 HTTP/2。
```

- **api 单 worker**:`uvicorn ... --port 8787` 无 `--workers`,即 1 进程 1 事件循环 1 核。所有 REST + SSE 都在这一个核上。
- **Redis 连接池**:`AsyncRedis.from_url(...)` 未设 `max_connections`(redis-py 默认无上限,受 OS fd 约束);`socket_timeout=None` 让 SSE 的阻塞 `xread block=30s` 自然等待 —— **每条 SSE 在 block 窗口内独占一条 Redis 连接**。
- **采集 user-independent**:collector 单实例采固定 CORE∪watchlist,与在线用户数无关 —— 这是**强项**,扩用户不增采集负载。

---

## 2. 连接模型 + 100 人定量估算

### 2.1 单用户连接数

| 类型 | 详情页 | 首页 |
|---|---|---|
| **持久 SSE** | **2 条**(`/sse/bars` K线 + `/sse/intraday` 分时)| **2 条**(`/sse/bars/batch` + `/sse/bars`)|
| **短连轮询(SWR)** | ~5-7 端点 @15-60s(CD信号/量能/资金流/筹码/quote/profile)| 指数卡/自选/信号 @60s + quote @15s |

### 2.2 100 人(各开 1 详情页)

- **≈ 200 条常驻 SSE 长连接**,全部压在单 uvicorn worker。
- 短连轮询:命中 Redis cache 时 ≈ 100 人 × ~6 端点 / 60s ≈ **10 req/s**,微不足道(只要 cache 命中,api 0 DB)。
- **持久连接本身不是瓶颈**(asyncio 单进程可挂数千 idle 连接)。**瓶颈在 §3.1 的 CPU 扇出。**

---

## 3. 瓶颈与撞墙点(按严重度)

### 🔴 硬墙(外部限制/安全,绕不过)

**W1. Alpaca 免费 IEX `trades` 30 符号上限**
美股实时(分时 + 进行中态)靠订阅 IEX `trades`,免费层一次最多 ~30 只(`ws_consumer._desired_trade_symbols` 已 cap 30)。单/少用户没问题;**100 人看 >30 只不同美股 → 超出的标的无实时**。
→ 必须升级 **Alpaca 付费(SIP/无上限)**,或改"服务端固定订阅热门 N 只 + 冷门降级轮询"。

**W2. refill 放大(数据源被刷爆 / 间接 DoS 自己的数据源)**
api cache miss → 发 `bus:bars.refill_request` → collector 真 `ak_call`/Alpaca 拉数据。单人无所谓;**100 个公网用户点冷门/任意标的 → 大量(失败的)ak_call 灌爆熔断器 + 抢限速配额 + 从同一 IP 加剧 sina 封禁**(B-sina 隐患被公网放大)。**机制详析见附录 A。**
→ api 侧 refill 必须加 **标的白名单(最关键)+ 去重(`state:inflight`)+ 独立令牌桶限流**。

**W3. 零鉴权 / 零限流**
公网裸奔,任何人可无限请求。
→ 前置 **网关(nginx limit_req / Cloudflare)+ 基本鉴权**。

### 🟠 架构性(单机单人设计的直接后果)

**W4. SSE 全局流 + 每消费者各自过滤(最核心扩展缺陷)**
`sse_bars._stream_gen` 让**每条 SSE 连接都读整条 `bus:bars.updated`,在 Python 里筛自己的 symbol**(`sse_bars.py:98`)。后果:任一根 bar(crypto 进行中态每秒数十条)被**全部 200 条连接各自反序列化 + 过滤一遍** → CPU = **O(用户数 × 全局消息速率)**,全压单核。100 人 + crypto 高频已吃紧,几百人单核打满、SSE 延迟飙升。

**W5. 单 uvicorn worker(无水平扩展)**
1 进程 1 核处理全部读 + 扇出。CPU 天花板。

**W6. 每条 SSE 占一条 Redis 阻塞连接**
200 条 SSE 各 `xread block=30s` → 最多 200 条 Redis 连接常驻。连接池无上限 → 撞 OS fd（默认 ulimit 256/1024)。

**W7. 历史 K 线读耦合 collector 单写进程**
详情页翻页 api→转发 collector `/internal/bars/history`,collector 用**同一 RW DuckDB 连接 + 进程锁**串行查(雷区 6 代价)。100 人同时翻页 → 在 collector 锁里排队,且和采集写抢同一进程。

### 🟡 运营短板

- **W8. Next.js dev 模式**:`next dev` 非生产构建(无优化、热更开销、单实例)。生产须 `next build && next start` 或静态导出 + CDN。
- **W9. 单 Redis**:100 人可扛,但单点;且承载 cache + bus + 200 SSE 连接,需监控内存/连接数。
- **W10. 无 HTTP/2**:浏览器 HTTP/1.1 单域名 ~6 连接上限,2 SSE + 多轮询挤占。反代开 h2 解决。
- **W11. 无可观测**:无 metrics/告警,100 人下出问题难定位(原则 5 V1 不上 Prometheus,公网需重新权衡)。

---

## 4. 容量估算(各墙在什么量级撞)

| 用户数 | 状态 |
|---|---|
| 1-10 | 全绿(当前设计目标)|
| ~30 | Alpaca 30 符号开始不够(若看 >30 只不同美股);其余宽裕 |
| ~100 | SSE 扇出单核吃紧(尤其 crypto 高频时段);refill/无鉴权成为公网风险;Redis 连接数需调 |
| 300+ | 单 worker CPU 打满、SSE 延迟显著;DuckDB 翻页排队;必须水平扩 + SSE 重构 |

---

## 5. 多用户化路线图(分优先级 + 工作量)

### P0 — 公网上线前的"安全闸"(不做不能放公网)

| 项 | 方案 | 量 |
|---|---|---|
| W3 鉴权/限流 | 前置 nginx/caddy(开 HTTP/2)+ `limit_req` 限流;或 Cloudflare。基本 token/口令鉴权 | 中 |
| W2 refill 放大 | api `/bars` cache miss 发 refill 前:`state:inflight` 去重 + 令牌桶限流 + **只允许 CORE∪watchlist 白名单**触发 refill(冷门标的直接返回 stale 不补)| 中 |
| W1 Alpaca 上限 | 决策:升级 Alpaca 付费(彻底)/ 或"固定订阅热门 + 冷门 SWR 降级"(免费但有限)| 取决于预算 |

### P1 — 读路径水平扩 + SSE 重构(支撑 100→数百)

| 项 | 方案 | 量 |
|---|---|---|
| W5 单 worker | uvicorn 多 worker(`--workers N`)或多实例 + nginx 反代负载均衡。SSE 无状态(只读 Redis),可水平扩 | 中 |
| W4 SSE 扇出 | **改服务端路由**:进程内"**一个 reader 读 bus → 按 symbol 分发给本地订阅者**"(内存 pub/sub),消费者不再各自全量过滤;或改 **per-symbol/per-interval 流**让每连接只读自己那条。把 O(用户×消息) 降到 O(消息)| 大(核心改造)|
| W6 Redis 连接 | 设 `max_connections` + 调 OS ulimit;多 worker 后每 worker 池独立,总连接 = worker×池 | 小 |

### P2 — 读侧解耦 + 生产化

| 项 | 方案 | 量 |
|---|---|---|
| W7 DuckDB 读耦合 | 读路径加 **Redis 缓存层 / 只读副本**:热点历史页缓存到 Redis,减少打到 collector;或 collector 开独立只读连接池(注意雷区 6 锁约束)| 中 |
| W8 Next.js | `next build && next start`(生产构建)+ 静态资源走 CDN | 小 |
| W9/W11 | Redis 监控 + api 基本 metrics(请求量/SSE 连接数/扇出延迟)| 中 |

---

## 6. 推荐目标架构(100 人稳态)

```
              Cloudflare / nginx(HTTP/2 + limit_req + 鉴权)
                              │
        ┌─────────────────────┼─────────────────────┐
   uvicorn api ×N(多 worker, 无状态读)  ← 负载均衡
        │  SSE: 每 worker 一个 bus reader → 进程内按 symbol 扇出给本地连接(W4)
        │  REST: 读 Redis cache; 历史读 Redis 热点缓存层(W7), miss 才转发 collector
        ▼
   Redis(cache + bus + 状态; 调 max_connections; 监控)
        ▲ 写
   collector ×3(单实例不变, 采集 user-independent)
        - 美股实时: Alpaca 付费(W1) 或 热门固定订阅
   DuckDB(collector 同进程 RW; 历史读优先走 Redis 缓存层兜)
   Next.js(next build/start + CDN)
```

要点:**采集侧完全不动**;改造集中在"api 多 worker + SSE 服务端路由 + 读缓存层 + 前置网关"。

---

## 7. 与现有设计原则的张力(需 zhonghuai 决策)

公网 100 人会**突破**当前几条原则,需重新权衡:
- **原则 4「不做用户系统」** → 公网至少要鉴权/限流(W3)。
- **原则 1「免费层优先」** → 美股实时 100 人需 Alpaca 付费(W1),否则只能"热门固定订阅"妥协。
- **原则 5「单一可跑, 不上 Prometheus/多实例」** → 100 人需多 worker(W5)+ 基本可观测(W11)。

**建议**:这三条在"公网多用户"语境下应显式更新。本文档作为该决策的输入。

---

## 8. 下一步建议

1. **先做 P0 安全闸**(鉴权/限流 + refill 防放大)——成本最低、收益最高,是公网上线的最低门槛。
2. **W1 Alpaca 决策**(付费 vs 妥协)——影响美股实时体验上限,需尽早定。
3. **W4 SSE 重构**是支撑 100+ 的核心工程,单独立计划(spec → plan)。
4. 现阶段若先小范围(≤20 人)灰度,可只做 P0 安全闸 + Next.js 生产构建,SSE/多worker 暂缓。

---

## 附录 A · W2 refill 放大机制详析

### A.1 链路(精确, 带行号)

```
前端请求 /api/symbols/{symbol}/bars  (用户在详情页 URL 输任意 code 都能触发)
  → svc.get_bars_cache_only(symbol)          # 只读 Redis cache, 不碰 DB / 不 ak_call
  → 若 cache 空 (not bars_list):
       _publish_refill_request(...)           # symbols.py:333 → xadd bus:bars.refill_request
       返回 stale "warming_up"
  ↓ (collector 侧)
  refill_consumer.consume_loop                # consumer group "collector", count=10 block=5s
  → handle_refill_message → refill_fn         # = kline.fetch_fresh_bars
  → adapter fetch → ak_call(sina) / Alpaca    # ← 真正打数据源, 经 ratelimit + breaker
```

### A.2 已有"刹车"(所以不是无限放大)

1. **bus `maxlen=100 approximate`**(`symbols.py:366`):refill 队列内存上限 ~100 条。
2. **单 consumer 串行**(`count=10 block=5s`):一次最多处理 10 条。
3. **ak_call 三层中间件**:sina 令牌桶(5/s)+ 熔断器 → raw req/s 被限速封顶。

→ "无限刷爆内存"不会发生。但下面 4 个缺失,让它在 100 人公网下仍能搞挂数据源。

### A.3 放大向量(缺失的防护)

1. **无去重 → N 用户 = N 次重复拉同一标的**:`_publish_refill_request` 发前无 `state:inflight` 去重。100 人同时开同一冷门标的 → 100 条 refill → consumer 逐条 → 同一标的 `fetch_fresh_bars` ~100 次(一次就够),浪费 100 份配额。
2. **无白名单 → 可触发任意标的(最危险)**:`get_bars_cache_only` 对任何 cache 没有的 symbol 都算 miss → refill。用户 URL 输任意 code(冷门/退市/垃圾)都触发 `fetch_fresh_bars`;垃圾/illiquid 标的 sina 返回空/错 → 这次 ak_call 记为**熔断器失败样本**。
3. **熔断器被灌爆 → 连带搞挂正常采集**:60s 窗失败率 ≥60% → open 5min。用户触发的冷门/垃圾拉取大量失败 → 失败样本灌满 → 熔断器 open → **共用同一 sina breaker 的 live 采集(8 指数/watchlist)一起被熔断**;半开探针又失败就再开,形成 ban 循环。**几个用户狂试乱码标的就能把整个 A 股采集熔断。**
4. **配额抢占 + 加剧封 IP**:refill 的 ak_call 与 live 采集**共用同一 sina 5/s 令牌桶** → refill 洪峰把桶吸干 → live 采集被饿死;且 100 人请求从**同一出口 IP** 发出,叠加 B-sina,**封 IP 概率与时长被推高**。

### A.4 一句话

不是"无限刷爆内存",而是:**公网用户可借后端、用任意标的触发失败的 ak_call,这些失败既灌爆熔断器(连带搞挂正常采集),又和正常采集抢同一限速配额,还从同一 IP 加剧 sina 封禁** —— 单人无害,100 人变成"用户可间接 DoS 自己的数据源"。

### A.5 修复(按重要度)

1. **白名单(最关键)**:只允许 **CORE ∪ watchlist** 标的触发 refill;不在名单的直接返回 stale,**绝不打数据源**。直接掐断"任意标的灌爆熔断器"。**对当前 B-sina 也立即减压。**
2. **去重**:发 refill 前 `state:inflight:{symbol}:{interval}`(短 TTL)挡重复,N 用户同标的只拉 1 次。
3. **独立限流预算**:refill 单独令牌桶(或全局 refill 上限),不与 live 采集抢配额,避免饿死正常采集。
