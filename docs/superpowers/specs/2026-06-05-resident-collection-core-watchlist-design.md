# 采集模型重构:固定核心标的 · 5m+1d 直取 · 其余全聚合 — 设计文档

> 日期:2026-06-05 · 状态:已对齐待审批(取代同日 resident-collection 初稿)
> 性质:**仅 collector 进程改动**。采集解耦前端订阅,改为对「后台固定核心标的」常驻采集;源头直取仅 5m+1d,其余周期全部聚合派生。crypto 不改(WS 原生)。

---

## 0. 采集原则(用户最终定义)

1. **采集名单 = 后台手动配置的固定核心标的**(`CORE_SYMBOLS`),A股15/美股10/crypto5。前端**无权增减、无权触发名单外标的**。
2. **名单内标的常驻采集**,与前端"有没有人看"完全解耦(去掉 SSE 订阅驱动采集)。
3. **源头直取仅 5m + 1d**;15m/30m/60m/4h 从 5m 聚合,1wk/1mo 从 1d 聚合(crypto 例外见下)。
4. **前端「自选/订阅」= 纯展示**:只能选名单内标的,看其历史 K线(各周期)+ 分时图(仅股市)。
5. **美股例外**:分时(1m/逐笔)不采;5m 往上照常。
6. **crypto 不改**:固定 5 币,WS 原生推全周期(含 1wk 周一锚点 / 1mo 月初锚点)。

---

## 1. 周期来源映射(防搞错,分市场)

```
A股 / 美股:
  直取:     5m、1d
  5m 聚合 → 15m、30m、60m、4h
  1d 聚合 → 1wk(W-FRI 周五锚点)、1mo(month-end 月末锚点)

crypto(全 WS 原生,不聚合):
  WS 直推:  5m、15m、30m、60m、4h、1d、1wk(周一锚点)、1mo(每月1号锚点)
```

**关键**:`W-FRI` 只用于 A股/美股的 1wk;crypto 的 1wk/1mo 走币安 WS 原生,锚点不同(周一/月初),**绝不可用 W-FRI 聚合 crypto**。

---

## 2. 现状与改动

| | 现状 | 改为 |
|---|---|---|
| A股采集驱动 | 订阅驱动 + 大盘默认 | **CORE 常驻**(去订阅驱动) |
| 美股采集驱动 | 订阅驱动(state:subscribe:us:*) | **CORE 常驻** |
| A股 15m/30m | 源头直取 | **从 5m 聚合**(取舍:省调用、口径统一;接受与直取的细微 OHLC 差异) |
| 美股 15m/30m | SIP 直取 | **从 5m 聚合** |
| 60m/4h | 5m 聚合(已是) | 不变 + 修触发可靠性 |
| 1wk/1mo | A股美股 1d 聚合 / crypto WS 原生 | 不变 |
| crypto | WS 原生全周期 | **不改** |
| 前端加自选触发采集 | 加自选发 refill 拉任意标的 | **限制:仅名单内有效;名单外不可触发** |

---

## 3. 具体改造(仅 collector)

### 3.1 A股 bar_poller
- 标的集:从「`_DEFAULT_SYMBOLS` + 扫 `state:subscribe:*`」改为 **`CORE_SYMBOLS['ashare']` 常驻**。
- 直取周期:**只 5m**(原 5m/15m/30m → 砍成只 5m)。1d 由现有 cron/直取路径。
- 15m/30m/60m/4h:全部由 5m 收线触发 `aggregate_and_publish` 聚合。
- 进行态:quote_bar_ticker 维持,覆盖 CORE 全集。

### 3.2 美股 bar_poller
- `_scan_symbols`:从扫 `state:subscribe:us:*` 改为 **`CORE_SYMBOLS['us']` 常驻**。
- 直取周期:只 5m(SIP);15m/30m/60m/4h 从 5m 聚合。1d 现有路径。
- 分时(1m/逐笔)不采(维持)。bar_ticker(trades 进行态)维持。

### 3.3 aggregate_derived 扩展 + 修复
- **新增 15m/30m 从 5m 聚合**(原它们直取,现纳入聚合 targets):`("15m","5m",15)`、`("30m","5m",30)`。
- **修 60m/4h 聚合触发可靠性**(消除实测 4h 落后 4 天)。
- 1wk/1mo 维持(A股美股 1d resample;crypto 不经此路)。

### 3.4 前端触发限制
- 加自选 refill(`_refill_new_symbol`):**加白名单校验——仅当 symbol ∈ CORE_SYMBOLS 才发 refill**;名单外标的前端不应能加(或加了也不触发采集)。
- (前端 UI 限制"只能选名单内标的"属前端改动,本轮聚焦后端不放行名单外触发;前端选择限制可另做。)

### 3.5 crypto
- 不改。WS 原生 8 周期常驻,周一/月初锚点正确。

---

## 4. 调用量评估

- A股:`15 标的 × 1 个直取请求(5m)× 每10s` = 1.5 req/s,远低于 sina 5/s。1d 低频。**比现状(5m/15m/30m 三请求)降 2/3**。
- 美股:10 标的 × 5m SIP × 60s,压力小。
- 聚合是本地 CPU,无外部调用。

---

## 5. 风险与回滚

- **15m/30m 聚合精度**:与直取有末位小数差异(同 60m/4h 现状)。若不可接受可回退直取。
- **B-sina**:常驻采集仍受 sina 封 IP 影响,但调用量降低反而减轻压力。
- 回滚:标的集来源 / 直取周期集 改回即恢复;各项独立。
- 无破坏性数据操作(不删历史)。

---

## 6. 改动范围

**仅 collector**:
- `apps/collector/ashare/bar_poller.py`、`apps/collector/us/bar_poller.py`:标的集 = CORE 常驻;直取砍到只 5m
- `apps/collector/ashare/main.py`、`us/main.py`:接线 CORE 标的集(去订阅驱动)
- `apps/collector/jobs/aggregate_derived.py`:加 15m/30m 聚合 + 修 60m/4h 触发
- `apps/api/routes/watchlists.py`:refill 加 CORE 白名单校验

**不碰**:crypto collector、api 读路径、前端(前端选择限制另议)、core 算法、DB schema、周线月线口径。

---

## 7. 待实施时定的小项
- 1d 直取在 A股/美股的具体 cron(现有路径沿用,实施时确认未被本次改动破坏)。
- quote_bar_ticker / bar_ticker 的覆盖标的集同步扩到 CORE 全集。
