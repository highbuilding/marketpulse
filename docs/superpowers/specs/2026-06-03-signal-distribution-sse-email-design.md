# CD 信号分发:前端实时推送 + 邮件兜底 — 设计文档

> 日期:2026-06-03 · 状态:已对齐待实施
> 性质:**后端 + 前端新功能**。给事件驱动 scan 加下游分发——scan 产新信号 → 发 `bus:signal.new` → 前端 SSE 实时推送(在线展示)+ 邮件 30 分钟攒批摘要(离线兜底)。两订阅者解耦,fire-and-forget。

---

## 0. 背景

上一轮把 CD scan 改成事件驱动(`scan_symbol_readonly`,订阅 `bus:bars.updated`)。但移除 `cd:* cron` 后,原挂在 cron 上的邮件通知(`maybe_send_summary`)断了。本轮把信号分发做成发布-订阅:scan 是上游生产者,前端 UI 和邮件模块是两个独立下游订阅者。

`bus:signal.new`(`core/cache/keys.py:21::BUS_SIGNAL_NEW`)已预留 key,本轮首次启用。

---

## 1. 架构总览

```
scan_symbol_readonly 产新信号(upsert_many 返回新增记录)
    │ 对每条新增信号 xadd bus:signal.new(fire-and-forget, 失败只 log)
    ▼
bus:signal.new(Redis Stream)
    ├──────────────────────────────┬─────────────────────────────┐
    ▼                              ▼
前端 SSE: /api/sse/signals         邮件: SignalDigestWorker(30min cron)
api 进程订阅 bus → 转发浏览器       查 SQLite indicator_signals 近 30min
全量推, 前端按当前市场过滤          → 按 recipient + symbol_config 过滤
在线展示, 离线无所谓               → 渲染摘要模板 → EmailChannel 发送
                                  → notification_audit 去重防重发
```

**核心原则**:两条路径独立。前端 SSE(实时性,走 bus)与邮件(可靠性,查库)互不依赖。scan 发完事件即返回,不等任何订阅者。

**关键取舍**:邮件**查 SQLite**(信号已落库,30min cron `SELECT detected_at 区间`,可靠、天然去重),不消费事件流。`bus:signal.new` 只服务前端 SSE。

---

## 2. 组件设计

### 2.1 发布点:scan_symbol_readonly 发 bus:signal.new
- 现 `upsert_many(records) -> int` 只返回新增数,拿不到"哪几条新增"。改为返回**新增记录列表**(或 upsert 前先查已存 bar_ts 集合做 diff)。
- 对每条**真新增**信号 xadd `bus:signal.new`,载荷:
  ```json
  { "market", "symbol", "interval", "signal_type", "bar_ts", "price", "detected_at" }
  ```
- fire-and-forget:xadd 失败只 `log.warning`,不影响 scan 主流程。

### 2.2 前端 SSE 端点:/api/sse/signals(api 进程)
- 订阅 `bus:signal.new`,转发给浏览器(复用 `apps/api/routes/sse_bars.py` 的 StreamHub 单读多分发模式)
- 事件类型 `signal`,data = §2.1 载荷
- 全量推送,**前端按当前市场过滤**(`inferMarket(symbol) === market`)

### 2.3 前端消费:概览"最近信号" + /signals 页
- **首屏**:打开/刷新时查**当天(市场交易日)**已发生信号——`listCDSignals({ market })` 后按市场交易日过滤(A股 BJT 自然日 / 美股 ET 交易日 / crypto BJT;复用 `tradingDateKey`)
- **实时追加**:EventSource 订阅 `/api/sse/signals`,收到新信号按市场过滤后插入列表顶部
- **去重**:按 `symbol+interval+bar_ts+signal_type` 幂等渲染(首屏历史 + SSE 增量合并去重)
- **隔天**:只展示当天交易日的,昨天的不显示(过滤条件每次渲染重算)

### 2.4 邮件攒批:SignalDigestWorker(30min cron)
- 挂 **ashare collector** 的 scheduler(它常驻 + 已有 sched),`_leader_gated` 包裹保证单机/多节点单发
- 每 30 分钟 tick:
  1. `SELECT * FROM indicator_signals WHERE detected_at >= now - 30min`
  2. 对每个启用的 recipient(`notification_recipients` where enabled):取其 `symbol_notification_config`(关心标的 + 勾选周期),筛出匹配信号
  3. 有匹配 → 渲染摘要模板(按市场/标的分组,行 = 买入/卖出 · 标的 · 周期 · 触发价 · 时间)→ `EmailChannel` 发送
  4. 无匹配 → 不发(空摘要不打扰)
- **去重防重发**:复用 `notification_audit`(snapshot_hash = 排序后信号集 sha256),同一批不重复发
- 复用 `NotificationService` / `EmailChannel`,不重写发送链路

### 2.5 完整性兜底:低频补扫 cron(SignalSweepWorker)
事件驱动保实时,但有两个漏扫缝隙:① `bus:bars.updated` 事件被 Redis Stream `maxlen=10000` 挤出,或 collector 发事件失败(只 log);② A股/美股 60m/4h 依赖 5m 收线触发 `aggregate_and_publish`,该步失败则不发 final 事件。纯事件驱动一旦丢事件就静默漏信号(cron 已移除,无补偿)。

- 每 30 分钟 cron(与邮件攒批同频),挂 ashare collector + `_leader_gated` 单发
- 对 CORE∪watchlist 全标的全信号周期(15m/30m/60m/4h/1d)逐个跑 `scan_symbol_readonly(symbol, interval)`
- **同一只读路径**:补扫只读已存 bar、upsert 幂等——漏的捞回,已有的不重复产信号,**不引入偏移**(偏移源是 `fetch_fresh_bars` 聚合,补扫不碰)
- 分工:实时性靠事件驱动(bar 收线即扫),完整性靠补扫 cron(漏的下次捞回)
- 补扫新增的信号同样发 `bus:signal.new`(前端/邮件下游一致)


---

## 3. 错误处理与边界

- scan 发 bus 失败:log,不阻塞(fire-and-forget)
- 前端 SSE 断线:EventSource 自动重连;断线期间的信号靠下次首屏查库补回(不保证 SSE 不丢,UI 不依赖它完整)
- 邮件 SMTP 未配置(无 `SMTP_*` env):`EmailChannel` 已有 `notify.email.disabled` 降级,cron 照跑但不发(dev 不受影响)
- 邮件 cron 多进程:仅 ashare collector 挂 + leader_gate,避免三进程重复发
- 信号密集:30min 攒批天然合并;前端 SSE 逐条推但幂等渲染

---

## 4. 迁移步骤

1. `upsert_many` 返回新增记录列表 + 单测(已存的不算新增)
2. scan_symbol_readonly 发 `bus:signal.new` + 单测(mock redis 验证 xadd)
3. api 加 `/api/sse/signals` 端点(复用 StreamHub)
4. 前端概览"最近信号" + /signals 加 EventSource + 当天交易日过滤
5. SignalDigestWorker(30min cron)+ 挂 ashare collector + leader_gate
6. SignalSweepWorker(30min 补扫 cron)+ 挂 ashare collector + leader_gate(§2.5 完整性兜底)
7. 验证:发一条 bus:signal.new → 前端实时显示;手动触发 digest → 邮件渲染(SMTP 未配则验证 disabled 降级);停 consumer 造一个漏事件 → 补扫 cron 捞回

---

## 5. 回滚

- 各步独立。发布点(步1-2)纯新增,不影响 scan。
- SSE 端点(步3-4):前端不接 EventSource 即回到 60s 轮询现状。
- 邮件(步5):不挂 cron 即不发邮件。
- 均无破坏性数据操作(不改 bar/信号库结构)。

---

## 6. 改动文件清单

**新增**:
- `apps/api/routes/sse_signals.py`:`/api/sse/signals` SSE 端点
- `apps/collector/jobs/signal_digest_worker.py`:30min 攒批邮件
- `apps/collector/jobs/signal_sweep_worker.py`:30min 补扫 cron(完整性兜底, §2.5)
- `apps/web/lib/use_signal_stream.ts`:前端 EventSource hook
- 测试:`tests/unit/services/test_upsert_returns_new.py`、`tests/unit/collector/test_signal_digest.py`

**改动**:
- `core/persistence/signal_repo.py`:`upsert_many` 返回新增记录
- `core/services/signal_service.py`:scan_symbol_readonly 发 bus:signal.new
- `apps/api/main.py`:挂 sse_signals 路由 + StreamHub lifespan
- `apps/collector/ashare/main.py`:挂 SignalDigestWorker cron
- `apps/web/app/page.tsx`、`apps/web/app/signals/page.tsx`:EventSource + 当天交易日过滤
- `apps/web/lib/signal_time.ts` 或 `markets.ts`:当天交易日过滤辅助(复用 `tradingDateKey`)

**不碰**:bar 采集链路、scan 只读逻辑、DB schema
