# Collector Cron 任务审计

> 起源:2026-05-28 的全量审计,目标"分析是否有多余的采集 + 验证非工作日/非开盘时间不采"。
> 美股口径:盘前 4:00-9:30 + RTH 9:30-16:00 + 盘后 16:00-20:00 (ET) 全部都要采。
>
> SSoT:闸门用 `core/domain/market_calendar.py::is_trading_day(market)` +
> `core/domain/market_sessions.py::is_market_session_open(market)`。
> 注册位置:`core/scheduler/scheduler.py`。

---

## 39 个 cron 任务总览

| ID | 频率/时刻 (TZ) | 触发函数 | 闸门 | 状态 |
|---|---|---|---|---|
| **tick:ashare** | 10s | `tick_snapshot_once` | 交易日 ✓ + session ✓ | OK |
| **tick:hk** | 10s | `tick_snapshot_once` | 交易日 ✓ + session ✓ | OK |
| **tick:us** | 10s | `tick_snapshot_once` | 交易日 ✓ + session(ET 4:00-20:00) ✓ | OK,**覆盖盘前+盘中+盘后** |
| **tick:crypto** | 10s | `tick_snapshot_once` | crypto 永远开 | ⚠️ coingecko 429 持续刷日志 → **本次禁用 crypto market** |
| **flush:all** | 60s | `flush_all_quotes_to_duckdb_async` | 无 | ⚠️ 应按 market 各自 session 过滤 quote 后再 flush(本次修复) |
| **index_minute:ashare** | 30s | `refresh_all_indices` | 交易日 ✓ + hour 9-17 BJT + cold-start refill | OK,TTL 24h |
| **index_minute:us** | 60s | `refresh_all_us_indices` | 交易日(us) ✓ + hour 4-21 ET + cold-start refill | OK,TTL 24h |
| **market_dashboard:ashare** | 60s | `refresh_dashboard_job` | 无 | ⚠️ 不打 ak(只读其他 cache 聚合)但盘外无意义,本次加 gate |
| **market_top:all** | 60s | `refresh_all_top_jobs` | 函数体内 per-market 交易日 ✓ + session ✓ | OK |
| **ai_packet:ashare** | 60s | `refresh_ai_packet` | 交易日 ✓ + session ✓ | OK |
| **ff:north** | 2min | `pull_north_flow_job` | 交易日(ashare) ✓ + session ✓ | OK |
| **ff:symbols** | 30min | `pull_watchlist_symbol_flow_job` | 交易日(ashare) ✓ + session ✓ | OK |
| **ff:purge** | 02:00 BJT 每日 | `purge_fund_flow_job` | 无 | OK(纯 SQLite 清理,无 ak) |
| **baseline_persist:ashare** | 15:35 BJT | `persist_ashare_baseline` | 函数体 `is_trading_day("ashare")` ✓ | OK |
| **baseline_persist:hk** | 16:05 BJT | `persist_hk_baseline` | 函数体 `is_trading_day("hk")` ✓ | OK |
| **baseline_persist:us** | 16:05 ET | `persist_us_baseline` | 函数体 `is_trading_day("us")` ✓ | OK(本次补) |
| **baseline_persist:cleanup** | 03:00 BJT 每日 | `cleanup_old_baselines` | 无 | OK(纯 SQLite 清理) |
| **chip:preload** | 15:35 BJT | `chip_service.preload_watchlist_chip_summary` | 函数体 `is_trading_day("ashare")` ✓ | OK(本次补) |
| **cd:15m** | mon-fri 09:30-15:30 BJT */15 | `scan_cd_job` | cron mon-fri + 函数体 is_trading_day(ashare) ✓ | OK |
| **cd:30m** | mon-fri 09:30-15:30 BJT */30 | `scan_cd_job` | 同上 ✓ | OK |
| **cd:60m:1030** | mon-fri 10:35 BJT | `scan_cd_job` | 同上 ✓ | OK |
| **cd:60m:1130** | mon-fri 11:35 BJT | `scan_cd_job` | 同上 ✓ | OK |
| **cd:60m:1400** | mon-fri 14:05 BJT | `scan_cd_job` | 同上 ✓ | OK |
| **cd:60m:1500** | mon-fri 15:05 BJT | `scan_cd_job` | 同上 ✓ | OK |
| **cd:4h:1130** | mon-fri 11:35 BJT | `scan_cd_job` | 同上 ✓ | OK |
| **cd:4h:1500** | mon-fri 15:05 BJT | `scan_cd_job` | 同上 ✓ | OK |
| **cd:1d** | mon-fri 15:30 BJT | `scan_cd_job` | 同上 ✓ | OK |
| **fetch:ashare:5m** | mon-fri 09:30-15:00 BJT */15 | `fetch_intraday_job` | cron mon-fri + 函数体 is_trading_day(ashare) ✓ | OK |
| **cd:us:15m** | mon-fri 04:00-19:45 ET */15 | `scan_cd_job` | cron mon-fri + 函数体 is_trading_day(us) ✓ | OK |
| **cd:us:15m:close** | mon-fri 20:30 ET | `scan_cd_job` | 同上 ✓ | OK(收尾扫盘后末根) |
| **cd:us:30m** | mon-fri 04:00-19:30 ET */30 | `scan_cd_job` | 同上 ✓ | OK |
| **cd:us:30m:close** | mon-fri 20:30 ET | `scan_cd_job` | 同上 ✓ | OK |
| **cd:us:60m:hourly_05** | mon-fri 05/06/07/08/09/16/17/18/19/20:05 ET | `scan_cd_job` | 同上 ✓ | OK(盘前/盘后整点+5) |
| **cd:us:60m:hourly_35** | mon-fri 09-15:35 ET | `scan_cd_job` | 同上 ✓ | OK(RTH 半小时收盘) |
| **cd:us:4h:hourly** | mon-fri 08/16/20:05 ET | `scan_cd_job` | 同上 ✓ | OK |
| **cd:us:4h:half_hour** | mon-fri 09/13:35 ET | `scan_cd_job` | 同上 ✓ | OK |
| **cd:us:1d** | mon-fri 16:30 ET | `scan_cd_job` | 同上 ✓ | OK(主) |
| **cd:us:1d:fallback** | mon-fri 20:30 ET | `scan_cd_job` | 同上 ✓ | OK(兜底,防主跑失败) |
| **fetch:us:5m** | mon-fri 04:00-19:45 ET */15 | `fetch_intraday_job` | cron mon-fri + 函数体 is_trading_day(us) ✓ | OK |
| **fetch:us:5m:close** | mon-fri 20:30 ET | `fetch_intraday_job` | 同上 ✓ | OK |

---

## 设计原则

1. **闸门双层**:cron `mon-fri` 排周末,函数体 `is_trading_day` 排节假日(春节/独立日/感恩节)
2. **每个 ak_call 类 job 都应当带交易日 + session 闸门**;非 ak 类(纯 SQLite 清理 / 聚合 cache)放宽
3. **美股要采 4-20 ET 全程**(盘前+盘中+盘后),`is_market_session_open("us")` SSoT 已包含三段
4. **cache TTL 24h**:让收盘后用户读到的是"最近交易日收盘价",而非 stale。一致用于 IndexCard / dashboard / top
5. **失败永远 try/except + WARN log**,优雅降级不 fail-fast(原则 2)

## 已知豁免

- **`crypto market` 已禁用** (`config/sources.yaml::crypto.enabled=false`):coingecko 免费版 429 限频严重,二级 binance/spot 接入待 Plan 4。
- **`hk index collector` 未实装**:`/api/indices/HSI.HK/minute` 返回 `stale=True, reason="hk_index_collector_pending"`。
- **`flush:all`**:用 `tick_snapshot_once` 写 quote 时已 session 过滤,所以 `flush:all` 不再额外 gate(本次修复)— quote cache 盘外为空,flush 自然空跑,不浪费 ak。

## 重启与验证

```bash
# 验证非交易时段 collector 静默
grep "tick.skip_off_session\|tick.skip_non_trading_day" /tmp/collector.log | tail -5
grep "ak_call.start" /tmp/collector.log | tail -5    # 应只在交易时段出现

# 验证 crypto 不再 429 刷屏
grep -c "coingecko HTTP 429" /tmp/collector.log    # 重启后应为 0
```

## 历史

- 2026-05-27 之前所有 job 都没有 session 闸门,整夜跑 ak_call。
- 2026-05-28 commit `cb7e8e9` 加 `is_market_session_open` SSoT + 5 个高频 job 闸门。
- 2026-05-28 commit `4bc186b` 给 `scan_cd_job` / `fetch_intraday_job` 加节假日闸门。
- 2026-05-28 本次审计:补 `flush:all` / `market_dashboard` / `chip:preload` / `baseline_persist:us` 漏网,禁用 `crypto market`。
