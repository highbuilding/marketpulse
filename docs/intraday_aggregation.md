# 60m / 4h K 线聚合规则与展示约束

> 起源:2026-05-21 美股切 SIP feed 后恢复 4h tab + 后续多次对齐讨论。
> 单一事实源:
> - bucket 网格 → `core/domain/market_sessions.py::SESSIONS / bucket_grid()`
> - 聚合实现 → `core/services/intraday_aggregator.py::aggregate_intraday()`
> - 前端 tab 可见性 → `apps/web/lib/intervals.ts::klineTabsForMarket / detailSignalTabs`

---

## 总原则

1. **bar.ts = bar close 时刻**(雷区 3 延伸到 intraday)。`(open_utc, close_utc]` 半开半闭,raw bar `ts == close` 算入当前桶,`ts == open` 算上一桶。
2. **桶起点对齐 session 起点**(富途口径),不是按 ET/BJT 自然 4h 网格。每个 session 内独立从 0 起切,**session 之间硬断**。
3. **末尾不足整 interval 自动成半棒**,而不是延伸到下一 session 也不是丢弃。这是富途客户端展示口径。
4. **raw bar 必须是 close 语义**:adapter 出口若新源是 START(Alpaca 60m/5m),要 `+freq` 转 close;sina A 股 5m `day` 字段已是 close,直通。

---

## A 股(SESSIONS:09:30-11:30 + 13:00-15:00,共 4h)

### 60m 富途口径 — **每日 4 根**

| 桶 | open(BJT) | close(BJT) → bar.ts |
|---|---|---|
| 1 | 09:30 | 10:30 |
| 2 | 10:30 | 11:30(上午尾棒,正好 1h) |
| 3 | 13:00 | 14:00 |
| 4 | 14:00 | 15:00(下午尾棒) |

> 注:上午 09:30-11:30 共 2h,正好 2 桶;下午 13:00-15:00 共 2h,正好 2 桶。**A 股 60m 没有半棒**。

`scan_cd_job` cron(`scheduler.py:120-126`,**写于 mon-fri 函数体内 is_trading_day("ashare") 二次过滤**):
- BJT 10:35 / 11:35 / 14:05 / 15:05(每根 close +5min,容错 sina 5m 收尾)

### 4h 富途口径 — **每日 2 根**

| 桶 | open(BJT) | close(BJT) → bar.ts | 说明 |
|---|---|---|---|
| 1 | 09:30 | 11:30 | 上午整段(2h,半棒) |
| 2 | 13:00 | 15:00 | 下午整段(2h,半棒) |

> A 股 session 总长 4h,理论上 1 根 4h 就够,但 session 硬断 → 上下午各 1 根半棒。

`cd:4h` cron:BJT 11:35 / 15:05(`scheduler.py:128-133`)。

---

## 美股 SIP feed(SESSIONS:04:00-09:30 盘前 + 09:30-16:00 RTH + 16:00-20:00 盘后,共 16h)

### 60m 富途口径 — **每日 17 根**

> 因 RTH 起点 09:30 不在整点上,**RTH 第 1 根是半棒(09:30-10:00)**。其它 session 起点都是整点。

| 桶 | open(ET) | close(ET) → bar.ts | session |
|---|---|---|---|
| 1 | 04:00 | 05:00 | pre |
| 2 | 05:00 | 06:00 | pre |
| 3 | 06:00 | 07:00 | pre |
| 4 | 07:00 | 08:00 | pre |
| 5 | 08:00 | 09:00 | pre |
| 6 | 09:00 | 09:30 | **pre 尾棒(半棒)** |
| 7 | 09:30 | 10:00 | **RTH 首棒(半棒)** |
| 8-13 | 10:00-15:00 | 11:00-16:00 | RTH 整点 6 根 |
| 14 | 15:00 | 16:00 | RTH 末棒 |
| 15-17 | 16:00-19:00 | 17:00-20:00 | post 整点 3 根 |

`cd:us:60m` cron(`scheduler.py:202-217`)被拆 2 条 cron 合并:
- 整点 +5: ET 05/06/07/08/09/16/17/18/19/20:05(盘前 6 根 + 盘后 4 根)
- 半小时 +5: ET 09/10/11/12/13/14/15:35(09:35 边界 + RTH 6 整点 + 14:35 等)

### 4h 富途口径 — **每日 5 根**

> session 起点(04:00 / 09:30 / 16:00)各自从 0 切,**RTH 6.5h** 切两段(4h 整 + 2.5h 半棒),**盘前 5.5h** 切两段(4h 整 + 1.5h 半棒),**盘后 4h** 1 根整。

| 桶 | open(ET) | close(ET) → bar.ts | session |
|---|---|---|---|
| 1 | 04:00 | 08:00 | pre 整 4h |
| 2 | 08:00 | 09:30 | pre 尾棒(1.5h 半棒) |
| 3 | 09:30 | 13:30 | RTH 整 4h |
| 4 | 13:30 | 16:00 | RTH 尾棒(2.5h 半棒) |
| 5 | 16:00 | 20:00 | post 整 4h |

`cd:us:4h` cron(`scheduler.py:219-231`)2 条:
- 整点 +5:ET 08:05 / 16:05 / 20:05
- 半小时 +5:ET 09:35 / 13:35

### Alpaca SIP 关键:`feed='sip'` + `end_safe = now - 20min`

- 1d 历史 1604 根起 2020-01-02(IEX 只到 2020-07-27 共 1462 根)
- 60m 16 根/日 完整覆盖 04:00-20:00 ET(IEX 时代只有 6-8 根)
- raw bar `b.timestamp` 是 **START** → 出口 `+freq` 转 close(`us.py:158-160`)
- free tier 受 15 分钟延迟限,代码留 20min 余量

---

## 港股(SESSIONS:09:30-12:00 + 13:00-16:00,共 5.5h)— **未实施 collector**

设计上 60m 应有 5 根 + 1 半棒,4h 应有 2 半棒。但 `/api/indices/HSI.HK/minute` 当前返回 `stale=True, reason="hk_index_collector_pending"`,Plan 4 候选。**前端 4h tab 对 HK 隐藏**。

---

## Crypto(SESSIONS:00:00-24:00,24/7)— 已禁用

`config/sources.yaml::crypto.enabled=false`(2026-05-28,coingecko 429 限频废)。等 Binance Spot 接入后再启。
设计口径:60m 24 根/日;4h 6 根/日。

---

## 前端 tab 可见性矩阵

| market | 1m | 5m | 15m | 30m | 60m | 4h | 1d | 1wk | 1mo |
|---|---|---|---|---|---|---|---|---|---|
| **ashare** 详情页 K 线 tab | ✓ | | | | | | ✓ | ✓ | |
| **ashare** CD 信号 tab | | | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| **us** 详情页 K 线 tab | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **us** CD 信号 tab | | | ✓ | ✓ | ✓ | ✓ | ✓ | | |
| **hk** 详情页 K 线 tab | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ |
| **hk** CD 信号 tab | | | ✓ | ✓ | ✓ | ✗ | ✓ | | |
| **crypto**(待启用) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

`klineTabsForMarket` / `detailSignalTabs`(`apps/web/lib/intervals.ts:34-60`)按 market 控制:
- `allowFourH = market === 'crypto' || market === 'us'`
- A 股详情页 K 线 tab 还做了精简,**只暴露 1m / 1d / 1wk**(用户偏好,看盘节奏)

---

## adapter ts 语义对照

| market | source | freq | 原始 ts 语义 | 出口处理 | 最终 bar.ts |
|---|---|---|---|---|---|
| ashare | sina(`stock_zh_a_minute`) | 5m | `day` 字段 = close | 直通(BJT → UTC) | close UTC |
| us | Alpaca SIP | 5m / 60m | `b.timestamp` = START | `+ timedelta(minutes=freq)` | close UTC |
| us | Alpaca SIP | 1d | ET 自然交易日 00:00 | 直通 | UTC `(D-1) 16:00`(雷区 3 1d 口径) |
| hk | (未实施) | — | — | — | — |
| crypto | (禁用) | — | — | — | — |

---

## 重采样 vs 切桶 — 不要混

- **5m / 15m / 30m / 60m / 4h**:`KLineService._get_intraday_aggregated()` 走 `aggregate_intraday()` 切桶,**5m raw 输入 → 60m / 4h 输出**(`kline_service.py:167-182`)。
- **1wk / 1mo**:`_resample()` 用 pandas `resample(W-FRI / ME)` 重采样 daily(`kline_service.py:250-269`)。
- **1m**:不重采样,从 adapter 直拉,**不入库 + 进程内 55s 短缓存**(`kline_service.py:134-165`)。

raw 输入是 5m,所以 60m 由 12 根 5m 聚合,4h 由 48 根 5m 聚合(美股 RTH 整 4h 桶)或 ≤48 根半棒。

---

## 历史与决策

- **2026-05-21** Alpaca IEX → SIP 切换(`docs/superpowers/specs/2026-05-21-us-alpaca-sip-and-4h-design.md`):IEX 4h 残缺 → SIP 16 根/日完整 → 4h tab 恢复。
- **2026-05-22 起** 60m / 4h 改走 `aggregate_intraday()` 切桶(之前是 pandas 整点 resample,跨 session 边界错位)。`market_sessions.SESSIONS` 升为 SSoT。
- **未来 TODO**(`docs/TODO.md`):ET 时钟对齐 4h bucket(让 4h 看上去更"自然"),但富途口径占优,目前不做。
