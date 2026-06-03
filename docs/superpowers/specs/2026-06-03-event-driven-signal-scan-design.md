# 事件驱动 CD 信号扫描(全市场)— 设计文档

> 日期:2026-06-03 · 状态:已对齐待实施
> 性质:**后端架构改造**。CD 信号 scan 从「cron 触发 + scan 时自拉自聚合」改为「`bus:bars.updated` 事件驱动 + 只读已存确定 bar」,全市场统一。根除 60m/4h 的 close/open 对齐偏移与双写覆盖。

---

## 0. 背景与根因(先读)

### 现状问题
CD 信号扫描当前由 cron 触发(`scheduler.py` 的 `cd:15m/30m/60m/4h/1d`),每次 scan 调 `SignalScanService.scan_many` → `KLineService.fetch_fresh_bars`。其中对 60m/4h(`_INTRADAY_AGG` 分支)的行为是:**现拉 5m → `aggregate_intraday` 现聚合 → `_persist_bars` 写回库 → 在聚合 bar 上算信号**。

### 根因(已实证)
`aggregate_intraday` 桶 key 用 `close_utc`(close 对齐,为股市设计)。crypto 应 **open 对齐**(雷区 3 例外)。于是:
- crypto backfill 把原生 60m/4h(open 对齐)写进 DuckDB ✅
- 但 scan 又用 5m 现聚合出 close 对齐的 60m/4h,在其上算信号、并 `_persist_bars` 覆盖
- 结果:库里 bar 是 open,信号 bar_ts 是 close(偏移一个 interval)

三市场核对实证(2026-01-01 ~ 06-03,crypto):系统性偏移在 30m(33 条)/60m(3)/4h(13)/1d(2)均存在,15m 无偏移(因 15m 不经聚合)。

### 设计决策
scan 改为**纯下游消费者**:不拉数据、不聚合、不写 bar,只读 DuckDB 已存确定 bar → `compute_cd_signals` → upsert 信号。bar 的生产口径完全由上游采集决定,scan 只信任库。由 `bus:bars.updated` 事件被动触发,全市场通用。

---

## 1. 架构总览

```
上游采集(各 collector, 不变)
  crypto WS 收线 / A股 bar_poller / 美股 bar_ticker+poller
    │ 写 DuckDB(原生 open 对齐 / 收线 close 对齐, 各市场口径)
    │ xadd bus:bars.updated { market, symbol, interval, ts, final }
    ▼
bus:bars.updated (Redis Stream, 已存在, 全市场已在发)
    │ final=true 且 interval ∈ SIGNAL_INTERVALS
    ▼
SignalScanConsumer(新增, 每 collector 内嵌一个, 各扫自己市场)
    │ scan_symbol_readonly(symbol, interval)
    ▼
SignalScanService.scan_symbol_readonly(新增)
    │ bar_repo 只读取已存 bar → compute_cd_signals → upsert SQLite
    ▼
indicator_signals (SQLite, 信号唯一事实源)
```

**核心原则**:scan 不 fetch、不 aggregate、不 persist bar。只读 + 算 + 写信号。

---

## 2. 组件设计

### 2.1 事件载荷(现成,无需改上游)
`bus:bars.updated` 的 payload(`_bar_to_event` / poller 构造,已确认):
```
{ market, symbol, interval, ts, open, high, low, close, volume, final }
```
`final=true` = 一根 bar 收线;`final=false` = 进行中态。

### 2.2 SignalScanService.scan_symbol_readonly(新增)
- 入参:`(symbol, interval)`
- 读 `bar_repo` 已存 bar(只读,lookback 取 `LOOKBACK_BARS[interval]` 根)
- `compute_cd_signals` → `repo.upsert_many`(SQLite,UNIQUE 幂等)
- **不** `fetch_intraday` / **不** `aggregate_intraday` / **不** `_persist_bars`
- 返回新增条数
- 老的 `scan_symbol`/`scan_many`(会 fetch+aggregate)保留作手动重扫工具,但不再被 cron 调用

### 2.3 SignalScanConsumer(新增)
- 订阅 `bus:bars.updated`(复用 `refill_consumer` 的 Redis Stream consumer group 骨架)
- 每条事件处理:
  1. 过滤:`final==true` 且 `interval ∈ SIGNAL_INTERVALS`(15m/30m/60m/4h/1d)。否则跳过(`final=false` 进行中态、1m/1wk/1mo 非信号周期)
  2. 调 `scan_symbol_readonly(symbol, interval)`
- 去重:`state:scanned:{symbol}:{interval}:{bar_ts}` 短 TTL 标记,防同一收线 bar 重复 compute(compute 幂等,去重仅省算力)
- 部署:每个 collector 进程内嵌,各只处理本市场事件(按 payload.market 过滤或各市场独立 stream 消费)

### 2.4 废弃
`scheduler.py` 移除 `cd:15m / cd:30m / cd:60m / cd:4h / cd:1d` 全部 cron job。

---

## 3. 迁移步骤

1. **加 `scan_symbol_readonly`**(纯新增,不动现有路径)+ 单测(固定 bar fixture 验证信号与口径)
2. **加 `SignalScanConsumer`**(纯新增,先不接线)+ 单测(mock 事件流验证过滤 + 去重)
3. **三 collector 接线 consumer + 摘掉 cron**(切换点):各 collector 启动跑 consumer;移除 `cd:*` cron。此后新信号全走事件驱动
4. **历史数据修正**(一次性,最敏感,见 §4)
5. **验证**:重跑三市场核对脚本,系统偏移归零

---

## 4. 历史数据修正

### 问题
库里存量信号有两类问题:close 对齐偏移(crypto 30m/4h 等)+ 历史回填缺口(1~4 月未扫)。

### 做法
- 对三市场全标的全信号周期,用 `scan_symbol_readonly` 读现有 DuckDB bar 重算
- **先删该 (symbol, interval) 的旧信号,再写新的**(保证 ts 口径统一,清除偏移残留)
- **顺带修复被污染的 crypto 60m/4h bar**:scan 曾用聚合 bar `_persist_bars` 覆盖过 → 重跑 crypto backfill 原生直取覆盖回来,再重算信号
- 一次性脚本 `apps/rescan_all_signals.py`(复用 `verify_all_signals.py` 的只读读 bar 模式 + 删旧写新)

### 备份
执行前 **备份 `data/state.db`**(信号库)。

---

## 5. 错误处理与边界

- consumer 单条事件失败:try/except 记 log,不阻塞后续事件(优雅降级,原则 2)
- `scan_symbol_readonly` 读库为空(bar 还没采到):返回 0,不报错;等下次 bar 更新事件再扫
- 实时性:bar 一收线即触发 scan,不慢于 cron;crypto 1d 低频也即时
- 信号 upsert 幂等:即便事件重发/重扫,UNIQUE 约束保证不产生重复

---

## 6. 回滚

- 步骤 1-2 纯新增,无影响
- 步骤 3(摘 cron)出问题:恢复 cron job 即回旧行为(consumer 可留,幂等不冲突)
- 步骤 4 改历史数据**不可逆** → 用 state.db 备份恢复

---

## 7. 验证

- 单测:`scan_symbol_readonly` 口径、consumer 过滤/去重
- 集成:发一条 `bus:bars.updated` final=true → 验证对应信号入库
- 数据:重跑 `apps/verify_all_signals.py`,三市场系统偏移(close/open +interval)归零
- `python -c "from apps.collector...main import app"` import 测试 + 三 collector 重启冒烟

---

## 8. 改动文件清单

**新增**:
- `core/services/signal_service.py`:`scan_symbol_readonly` 方法
- `apps/collector/jobs/signal_scan_consumer.py`:`SignalScanConsumer` + consume loop
- `apps/rescan_all_signals.py`:历史修正一次性脚本
- `tests/unit/services/test_scan_readonly.py`、`tests/unit/collector/test_signal_scan_consumer.py`

**改动**:
- `apps/collector/{ashare,us,crypto}/main.py`:启动接线 consumer
- `core/scheduler/scheduler.py`:移除 `cd:*` cron job

**不碰**:`aggregate_intraday`(股市派生仍用)、各 adapter、前端、DB schema
