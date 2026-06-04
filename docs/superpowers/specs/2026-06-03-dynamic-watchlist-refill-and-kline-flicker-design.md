# 动态自选即时采集 + K线首屏闪烁修复 — 设计文档

> 日期:2026-06-03 · 状态:已对齐待实施
> 性质:① 后端 — 加自选时发 refill 事件,collector 立即回补该标的历史(B2 事件驱动);② 前端 — 修 K线切周期"先一根后一堆"闪烁。

---

## 0. 背景与根因

### 问题 1:动态加的自选标的不被采集(MRVL 各周期无数据)
- 现状:美股 collector 的采集标的集 `_sweep_syms = CORE ∪ watchlist` 在**启动时算一次**(`apps/collector/us/main.py:207`),写死传给 `sweep_derived` cron 和 `startup_reconcile`。
- 根因:启动后动态加的自选(MRVL)不在启动快照里 → sweep/reconcile/backfill 都不采它 → 库里无 bar → 各周期无数据。
- 实证:MRVL 在自选(`我的关注`)、在 `symbol_directory`(可搜索),但 `bars_us.duckdb` 无 MRVL bar。

### 问题 2:K线切周期/切标的时"先一根后一堆"闪烁
- 现状:`apps/web/app/symbol/[code]/page.tsx:75-77`:`displayBars = mergeBarsAsc(hist.bars, streamBars)`。
- 根因:切周期时 `hist.bars`(REST 首屏 500 根)异步未返回,但 `streamBars`(SSE 实时尾部)单根先到 → 图表先渲染 1 根 → REST 500 根回来才补全 → 视觉"先一根后一堆"闪烁。与概览 pctChange 单根 bug 同源。

---

## 1. 问题 1 修法:B2 事件驱动即时回补

**复用现有 refill 链路**(无需新建机制):
- `bus:bars.refill_request`(`core/cache/keys.py:23`)已存在。
- 各 collector 已订阅:`us/main.py:112 consume_loop(refill_fn=_refill_dispatch)` → 收到请求即 `fetch_fresh_bars` 拉数据入库(crypto/ashare 同构)。

**改动**:加自选的后端入口 `apps/api/routes/watchlists.py::add_symbol`(100行)在 `svc.add_symbol` 成功后,对该 symbol 的各信号周期发 `bus:bars.refill_request`:
- 周期集:`SIGNAL_INTERVALS`(15m/30m/60m/4h/1d)+ 详情页常用(5m, 1wk, 1mo 可选;先覆盖信号周期 + 5m)
- 载荷:`{market: infer_market(symbol), symbol, interval, days}`(days 按周期取合理回补窗口,复用 refill_consumer 既有默认)
- 对应市场 collector 的 refill consumer 收到 → `fetch_fresh_bars`(原生直取)拉历史入库
- **fire-and-forget**:发 refill 失败只 log,不阻塞 add_symbol 返回(优雅降级,原则 2)

**效果**:加自选后几秒内,该标的各周期历史被拉入库,前端即可看到 K线。MRVL 这类动态标的不再需要重启 collector。

**为什么不动 sweep 启动快照**:sweep 仍按启动快照跑(它管的是已有标的的派生周期增量),新标的的"首次历史回补"由 refill 事件即时处理。两者职责分开,改动最小。

---

## 2. 问题 2 修法:首屏历史未到时不渲染单根

**改动**:`apps/web/app/symbol/[code]/page.tsx` 的 `displayBars`:
```js
// 现在
const displayBars = useMemo(() => mergeBarsAsc(hist.bars, streamBars), [hist.bars, streamBars])
// 改为: hist 首屏(500根)未到时不渲染 stream 单根, 避免"先一根"闪烁
const displayBars = useMemo(
  () => (hist.bars.length === 0 ? [] : mergeBarsAsc(hist.bars, streamBars)),
  [hist.bars, streamBars],
)
```
- 切周期时:`hist.bars` 先空(图表显示"加载中"或空态)→ REST 500 根回来 → 一次性完整渲染 + merge 实时尾部。无单根闪烁。
- 概览页若有同源问题,同样处理(本轮聚焦详情页;概览自选价格已在之前 pctChange 修过)。

---

## 3. 错误处理与边界

- refill 发送失败:log,不阻塞 add_symbol(用户加自选照常成功,采集稍后由 sweep 兜底)。
- 重复加同一标的:add_symbol 幂等(UNIQUE),refill 重发无害(fetch_fresh_bars upsert 幂等)。
- 非美股标的(A股/crypto):同样发 refill,对应市场 collector 各自消费(crypto 原生直取、A股 sina)。
- 前端 hist.bars 空态:图表已有"加载中/无数据"占位(KLineChart 现有逻辑),不需新增。

---

## 4. 迁移步骤

1. 后端:`add_symbol` 成功后发 refill 请求(各信号周期 + 5m)+ 单测(mock redis 验证 xadd)
2. 前端:`displayBars` 首屏 gate(hist 空不渲染 stream)+ tsc 验证
3. MRVL 验证:加自选 → 数秒后查库 MRVL bar 入库 → 前端各周期显示
4. 闪烁验证:切周期不再"先一根后一堆"

---

## 5. 回滚

- 后端 refill 发送是新增(纯增量),去掉即回原行为(新标的等 sweep 周期)。
- 前端 displayBars gate 改一行,回退即恢复无条件 merge。
- 均无破坏性数据操作。

---

## 6. 改动文件清单

**改动**:
- `apps/api/routes/watchlists.py`:add_symbol 发 refill 请求
- `apps/web/app/symbol/[code]/page.tsx`:displayBars 首屏 gate

**新增**:
- `tests/unit/api/test_watchlist_add_refill.py`:验证加自选发 refill

**复用(不改)**:`bus:bars.refill_request`、各 collector refill consumer、fetch_fresh_bars
**不碰**:sweep 启动快照逻辑、采集核心链路、DB schema
