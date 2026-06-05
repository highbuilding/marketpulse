# 采集模型重构:CORE∪自选 常驻全周期采集 — 设计文档

> 日期:2026-06-05 · 状态:待审批
> 性质:**仅 collector 进程改动**。A股/美股 K线采集从「SSE 订阅驱动」改为「CORE∪自选 常驻采集」,5m 往上每根 K线无人看也采集/更新/入库。crypto 已符合,不改。

---

## 0. 采集原则(用户定义)

1. **无人订阅也采** — 采集与前端"有没有人看"彻底解耦。
2. **采集范围 = CORE(默认大盘)∪ 自选标的**。
3. **5分钟周期往上每根 K线都采集/更新/入库**:5m/15m/30m/60m/4h/1d/1wk/1mo,不漏周期。
4. **例外**:美股因 trade 通道限制,分时(1m/逐笔)可不采;5m 往上照常。
5. **新订阅标的**:加入时触发历史回补(已有加自选 refill 雏形)。

---

## 1. 现状与差距

| | A股 | 美股 | crypto |
|---|---|---|---|
| 收线源 | bar_poller 10s 轮询 sina | bar_poller 60s 轮询 REST SIP | WS 推送 |
| 进行态 final=false | quote_bar_ticker | bar_ticker(trades) | WS k.x=false |
| 5m/15m/30m | 各自直取(并发 ak_call) | 各自直取 SIP | WS 原生 |
| 60m/4h | 5m 收线聚合 | 5m 收线聚合 | WS 原生 |
| **采集驱动** | **订阅驱动** + 大盘默认始终轮询 | **订阅驱动**(state:subscribe:us:*) | CORE 全周期 WS 常驻 |

**差距**:
- A股/美股 **订阅驱动** → 非订阅标的(CORE 个股、自选)的 15m/30m 平时不采(实测 600519 的 15m/30m 空)。
- 聚合触发不可靠:5m 收线应同步驱动 60m/4h,但实测 4h 落后 4 天。
- crypto 已符合原则(WS 8 周期常驻),**不改**。

---

## 2. 方案:常驻标的集 = CORE∪自选

### 2.1 A股 bar_poller 改造
- **标的集来源**:从"`_DEFAULT_SYMBOLS` + 扫 `state:subscribe:*`"改为 **`CORE_SYMBOLS['ashare'] ∪ watchlist.dynamic_universe()`**,常驻轮询。
- **周期**:对每个标的常驻直取 **5m/15m/30m**(各自 ak_call,与现状一致);60m/4h 由 5m 收线聚合;1d 由现有 cron/直取。
- **进行态**:quote_bar_ticker 维持(进行中 final=false),覆盖标的集同步扩到 CORE∪自选。
- **动态增减**:自选变动 → 轮询任务集动态加/删(加时配合已有 refill 回补历史)。
- **保留**:`state:subscribe` 机制可留作"前端实时加速提示",但**采集不再依赖它**。

### 2.2 美股 bar_poller 改造
- 同 A股:`_scan_symbols` 从扫 `state:subscribe:us:*` 改为 `CORE_SYMBOLS['us'] ∪ watchlist`。
- 周期:5m/15m/30m REST SIP 直取(SIP ~15-20min 延迟,符合现状);60m/4h 聚合。
- **分时不采**:1m/逐笔维持现状(trade 通道限制,符合原则例外)。bar_ticker(trades 进行态)维持,仅对"正在看"的标的(LRU-30)做实时——这是实时加速,不影响 5m 往上的收线采集。

### 2.3 crypto
- **不改**。WS 对 CORE 8 周期常驻推送 final=true/false,已符合原则。
- (自选的 crypto 标的若不在 CORE,需确认 WS 订阅集是否含自选 → 见 §4 待确认项)

### 2.4 聚合可靠性修复
- 修 `aggregate_and_publish` 对 60m/4h 的滚动触发:确保每次 5m 收线都正确驱动 60m/4h 桶滚动+写入(消除 4h 落后)。
- 1wk/1mo:从 1d resample,确保 1d 更新后触发。

### 2.5 历史回补(新订阅标的)
- 已实现:加自选 → 发 refill → collector 拉历史(本轮已上线)。
- 本方案补充:新标的加入常驻轮询集后,首次轮询即开始持续更新。

---

## 3. 调用量评估(B-sina 约束)

- A股常驻轮询:`|CORE∪自选| 标的 × 3 周期(5m/15m/30m) × (每10s)`。
- 若 CORE∪自选 ~20 标的 → 60 请求/10s = 6 req/s,sina 限频 5/s burst 20 —— **接近上限,需评估**:可能要拉长非 5m 周期的轮询间隔(15m/30m 不必 10s,可 30-60s)。
- 美股 SIP 60s 轮询,压力小。
- **设计决策**:15m/30m 轮询间隔与 5m 解耦(5m 保 10s,15m/30m 用 30-60s),降低 sina 压力。

---

## 4. 待确认项

- crypto 自选标的若不在 CORE 的 WS 订阅集,是否需动态加入 WS streams?(crypto WS 是启动时构建 streams URL,动态加标的需重连)
- 自选删除后是否立即停止采集(还是保留已采历史、只停增量)?建议:停增量轮询,保留历史。

---

## 5. 改动范围

**仅 collector**:
- `apps/collector/ashare/bar_poller.py`:标的集来源 CORE∪自选 + 轮询间隔分级
- `apps/collector/us/bar_poller.py`:_scan_symbols 改 CORE∪自选
- `apps/collector/ashare/main.py`、`us/main.py`:接线标的集 + 自选变动动态增减
- `apps/collector/jobs/aggregate_derived.py`:修 60m/4h 聚合触发可靠性
- crypto:不改(除非 §4 确认要动态 WS 订阅)

**不碰**:api、前端、core 算法、DB schema。

---

## 6. 风险与回滚

- B-sina 限频:轮询间隔分级缓解;若仍超限,缩小常驻集或拉长间隔。
- 回滚:标的集来源改回订阅驱动即恢复;各改动独立。
- 无破坏性数据操作。
