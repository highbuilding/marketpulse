# 多用户化(~100 并发 / 邀请制 VPS)技术设计

> 把 `docs/2026-06-02-multiuser-vps-scalability-review.md` 的评审结论落成可实施的具体技术方案。

- **日期**:2026-06-02
- **作者**:zhonghuai + Claude
- **状态**:设计已对齐(决策锁定),待落实施计划
- **评审来源**:`docs/2026-06-02-multiuser-vps-scalability-review.md`(瓶颈 W1-W11)

---

## 0. 背景 / 范围 / 锁定决策

**背景**:架构原为"单机单人本地工具",现要部署到公网 VPS、**邀请制(口令)熟人圈、扛 ~100 并发**。采集侧 user-independent(单实例 collector)不动,本设计只改**读 + 实时下发侧 + 部署**。

**锁定决策(zhonghuai 2026-06-02)**:
1. **鉴权**:应用层 cookie(登录页 + 共享口令 → 签名 HttpOnly cookie + FastAPI 中间件)。不做完整用户系统。
2. **SSE**:方案 A —— 每 worker 进程内"单读多分发"hub(消息只解析一次,按 symbol 分发)。
3. **美股实时**:维持免费 IEX,**纯 LRU-30**(按最近访问轮换 trades 订阅,不 pin CORE);挤出的标的无实时分时,前端提示。
4. **refill 防护**:白名单(仅 CORE∪watchlist 可触发)+ 去重(inflight)。
5. **水平扩**:uvicorn 多 worker + nginx(TLS/HTTP2/限流);Next.js 生产构建。
6. **读缓存**:历史分页加 Redis 页缓存,卸载 collector。

**不在范围**:Alpaca 付费(暂不,LRU-30 妥协);完整用户/权限系统;B-sina 根治(另案,见采集审计);Prometheus/Grafana(仅加轻量计数日志)。

---

## 1. 目标架构

```
          nginx (TLS + HTTP/2 + limit_req 限流 + SSE proxy_buffering off)
                    │  (同源, EventSource 自动带 cookie)
        ┌───────────┴───────────┐  轮询(无需 sticky)
   uvicorn api ×N workers   (api 无 V8/无 ak_call → 多 worker 安全)
     每 worker:
       · auth 中间件(校验签名 cookie)
       · SSE hub ×2(bars / intraday): 各 1 条 Redis 读连接, 解析一次→进程内分发
       · REST 读 Redis cache; 历史读 Redis 页缓存(miss 才转发 collector)
                    │
              Redis(cache + bus + state; 每 worker max_connections 限定)
                    ▲ 写                              ▲ 读(页缓存 miss)
        collector ×3(不动)                     collector /internal/bars/history
          美股 ws_consumer: trades LRU-30 订阅管理器(state:us:viewed ZSET)
        Next.js: next build && next start(生产构建)
```

**关键不变量**:collector 三进程完全不动(采集 user-independent);所有改动在 api / nginx / 前端登录页 / ws_consumer 订阅选择。

---

## 2. 工作包 ① 安全闸(鉴权 + refill 防护)

### 2.1 鉴权(应用层 cookie)

**组件**:`apps/api/auth.py`
- **登录**:`POST /api/auth/login {passcode}` → 校验 `passcode ∈ 允许集`(env `APP_PASSCODES` 逗号分隔,支持多邀请码)→ 用 `itsdangerous.URLSafeTimedSerializer`(密钥 env `APP_SECRET`)签发 token,`Set-Cookie: mp_session=<token>; HttpOnly; SameSite=Lax; Max-Age=2592000`(30 天)。
- **登出**:`POST /api/auth/logout` → 清 cookie。
- **中间件**:`AuthMiddleware`(Starlette `BaseHTTPMiddleware`)校验 `mp_session` cookie(验签 + 未过期)。
  - 豁免路径:`/api/auth/login`、`/api/health`、`/login`(前端页)、静态资源。
  - 失败:`/api/*`、`/api/sse/*` → `401`;浏览器页面请求 → `302 → /login`。
- **前端**:Next.js `/login` 页(输口令 → POST /api/auth/login → 成功跳首页)。未登录访问被中间件挡。
- EventSource 同源默认带 cookie → SSE 自动鉴权。

**错误处理**:`APP_SECRET`/`APP_PASSCODES` 未配置 → 启动 fail(公网必须配,不降级)。

**测试**:中间件单测(有效/过期/伪造 cookie → 放行/401);login 路由(对/错口令)。

### 2.2 refill 防护(W2,同时给 B-sina 减压)

**改 `apps/api/routes/symbols.py::_publish_refill_request`** 调用前加两道闸:
- **白名单**:`symbol ∈ CORE∪watchlist`(`core_symbols(market) ∪ dynamic_universe`,结果缓存 Redis `cache:refill_whitelist:{market}` TTL 60s 避免每次查 SQLite)。不在 → 不发 refill,直接返回 stale。
- **去重**:`redis SET state:inflight:refill:{market}:{sym}:{iv} 1 NX EX 60` 成功才发;已存在 → 跳过。refill_consumer 处理完 `DEL`(或靠 TTL)。

**测试**:白名单外标的不触发 publish;同标的并发只发一次。

### 2.3 nginx(运维配置,出 `deploy/nginx.conf` 样例)

TLS + HTTP/2;`limit_req zone` 每 IP 限速(如 REST 20r/s burst 40);SSE location `proxy_buffering off; proxy_read_timeout 1h; proxy_set_header Connection ''`(h1.1 长连)。upstream 轮询多 worker。

---

## 3. 工作包 ② SSE hub(方案 A)

### 3.1 组件:`apps/api/sse_hub.py`

```
class StreamHub:
    bus_channel: str                                  # bus:bars.updated 或 bus:intraday.updated
    key_fn: Callable[[payload]] -> hashable           # bars: (symbol,interval); intraday: symbol
    _registry: dict[key] -> set[asyncio.Queue]
    async def run():                                  # 单 xread 循环, 每 worker 一个
        last_id = "$"
        while not stopped:
            entries = xread({channel: last_id}, count=50, block=...)
            for msg: payload = json.loads(once); for q in _registry.get(key_fn(payload), ()): q.put_nowait(payload)(满则丢最旧)
    def register(key) -> Queue   # SSE 连上时
    def unregister(key, q)       # SSE 断开时(finally)
```

- **lifespan**:worker 启动时 `bars_hub = StreamHub(BUS_BARS_UPDATED, key=(sym,iv)); intraday_hub = StreamHub(BUS_INTRADAY_UPDATED, key=sym)`,各 `create_task(hub.run())`;关停 cancel。
- 存进 `app.state`,SSE 端点取用。

### 3.2 改造 SSE 端点

`sse_bars.py` / `sse_intraday.py` 的 `_stream_gen`:
- 不再自己 `xread`;改为 `q = hub.register((symbol,interval))`,循环 `await q.get()` → yield;`finally: hub.unregister(...)`(资源释放)。
- init 快照仍读 `cache:bars:*:current` / `cache:intraday:*:current`(不变);ping 不变。
- 背压:Queue `maxsize`(如 100),满则丢最旧(进行中态丢帧无害)。

**效果**:解析次数 = 消息速率(与连接数无关);Redis 连接 = 每 worker 2 条(两 hub)。多 worker:各 worker 独立 hub(xread `$` 各拿全量),客户端连任一 worker 都收到 → 无需 sticky。

**测试**:hub 单测(publish 一条 → 只有注册了该 key 的 Queue 收到;其它不收);register/unregister 后计数正确;Queue 满丢最旧。

---

## 4. 工作包 ③ 多 worker + 生产化

- **api**:生产用 `uvicorn apps.api.main:app --workers N`(N≈核数-2)或 `gunicorn -k uvicorn.workers.UvicornWorker -w N`。**api 无 V8/无 ak_call → 多 worker 安全**(雷区 1 只在 collector;dev 仍单 worker 不变)。出 `deploy/` 生产启动脚本/compose。
- **Redis 池**:`make_redis` 加 `max_connections`(如 50)防 fd 爆。
- **Next.js**:`next build && next start`(生产);静态资源走 nginx/CDN。
- **collector 不动**(单实例 leader 锁)。

**测试**:多 worker 下 SSE 端到端(连不同 worker 都能收到同一 symbol 的 push);Redis 连接数受控。

---

## 5. 工作包 ⑤ 美股 trades LRU-30

- **访问热度**:Redis ZSET `state:us:viewed`(member=symbol, score=epoch 秒)。分时 SSE 连上/心跳时,对 US symbol `ZADD state:us:viewed <now> <sym>`(在 `sse_intraday` 或 hub register 钩子)。
- **订阅选择**:`ws_consumer._desired_trade_symbols` 改为 `ZREVRANGE state:us:viewed 0 29`(最近 30)替代现 `sorted()[:30]`;维护 `state:us:realtime_active`(SET = 当前订阅的 30)。挤出的发 unsubscribe。
- **前端提示**:`/api/symbols/{s}/intraday-line` 响应 + 分时 SSE `connected` 事件带 `realtime: bool`(`sym ∈ state:us:realtime_active`)。前端 `IntradayLineChart`:`realtime=false` 时顶部提示"超出实时名额,分时暂不可用(K 线正常)"。
- **纯 LRU 不 pin CORE**(按决策 3)。CORE 的收线 bar 仍由 REST poller 采(不受影响),只是无人看时无 live 分时。

**测试**:ZREVRANGE 取 top-30;realtime flag 计算;挤出标的 unsubscribe。

---

## 6. 工作包 ④ 读缓存(历史分页脱离 collector)

- `symbols.py::bars_history`:先查 Redis `cache:barspage:{market}:{sym}:{iv}:{before}:{limit}` → 命中返回;miss → 转发 collector → 回写缓存。
- **TTL 按可变性**:`before` 非空(历史游标页,收线后不可变)→ 长 TTL(如 1 天);`before` 空(最新页,含进行中)→ 短 TTL(如 30s)或不缓存。
- 命中率高的"向左翻页历史"几乎全走 Redis,卸载 collector 锁竞争。

**测试**:命中/miss;历史页长 TTL、最新页短 TTL/不缓存。

---

## 7. 错误处理 / 优雅降级(贯穿)

- auth 未配置 secret/passcode → 启动 fail(公网安全,不降级)。
- hub xread 失败 → 重连退避,不影响已连接(连接读自己 Queue);某连接 Queue 满 → 丢最旧帧(进行中态无害)。
- refill 白名单/去重失败 → 保守不发(宁可 stale 不打源)。
- 页缓存 Redis 失败 → 退回直接转发 collector(降级)。
- LRU:`state:us:viewed` 读失败 → 退回订阅 CORE.us(保底)。

---

## 8. 实施分期(每包 → 独立 plan)

按"可独立交付 + 依赖"排序:
1. **① 安全闸**(鉴权 + refill 防护)——公网上线最低门槛,独立。**(顺带给 B-sina 减压)**
2. **② SSE hub**——核心,独立(单 worker 下即可验证)。
3. **③ 多 worker + 生产化**——依赖 ②(hub 必须 per-worker);含 nginx + Next.js prod。
4. **⑤ 美股 LRU-30**——独立(改 ws_consumer + 前端提示)。
5. **④ 读缓存**——独立,优先级最低(性能优化)。

每包一份 writing-plans 计划,TDD 落地。

---

## 9. 与设计原则的张力(需在 CLAUDE.md 显式更新)

- 原则 4「不做用户系统」→ 公网加轻量口令鉴权(非完整用户系统,折中)。
- 原则 1「免费层优先」→ 美股实时维持免费(LRU-30 妥协,不上 Alpaca 付费)。
- 原则 5「单一可跑/不上多实例」→ api 多 worker(collector 仍单实例)。

落地后更新 CLAUDE.md 第 0 章注脚:"公网多用户部署形态"作为单机形态之外的并列形态。

---

## 附录 · SSoT 影响清单

| 概念 | 位置 | 改动 |
|---|---|---|
| 鉴权中间件/登录 | `apps/api/auth.py`(新)+ `apps/api/main.py` 挂中间件 | 新建 |
| 前端登录页 | `apps/web/app/login/page.tsx`(新) | 新建 |
| SSE hub | `apps/api/sse_hub.py`(新)+ 改 `sse_bars.py`/`sse_intraday.py` | 新建+改造 |
| refill 防护 | `apps/api/routes/symbols.py::_publish_refill_request` | 加白名单+去重 |
| 美股 LRU | `apps/collector/us/ws_consumer.py::_desired_trade_symbols` + `state:us:viewed`/`realtime_active` key | 改造 |
| 历史页缓存 | `apps/api/routes/symbols.py::bars_history` + `core/cache/keys.py` 加 barspage key | 加缓存 |
| 部署 | `deploy/nginx.conf` + 生产启动脚本(新) | 新建 |
| Redis 池 | `core/cache/redis_client.py::make_redis` | 加 max_connections |
