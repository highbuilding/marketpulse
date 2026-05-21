# 美股 Alpaca SIP feed 切换 + 恢复 4h tab

**日期**: 2026-05-21
**版本**: 1.0
**作者**: zhonghuai + Claude
**状态**: 待实施

---

## 0. 背景

2026-05-20 USAdapter 接入 Alpaca IEX feed (free tier),实测可用但有局限:
- IEX 仅占美股全市场成交量 ~2-3%(1 家交易所), 历史窗口起点为 2020-07-27
- prepost(盘前/盘后)bar 稀疏, 60m 一日通常只有 6-8 根 → 之前结论"4h 重采样残缺", 故美股 4h tab 在前端被禁用、scheduler `cd:us:4h` 也被删除

实测 (2026-05-21): Alpaca free tier 在 `feed='sip'` + `end <= now - 15min` 下也能拿数据 (官方"15min 延迟"约束):
- 1d: 1604 根 (2020-01-02 起, 比 IEX 多 ~142 根)
- 60m: 16 根/日 稳定 (04:00-19:00 ET 全覆盖, 含完整盘前/盘中/盘后)
- 1m / 5m / 15m / 30m: 同样完整覆盖

→ 切 SIP 后 4h 重采样可拿到稳定 4 根/日 (16 ÷ 4), 4h tab 恢复条件成熟。

---

## 1. 目标

- **A. USAdapter feed 切换**: `_fetch_history_alpaca` + `_fetch_intraday_alpaca` 两处 `feed='iex'` → `feed='sip'`
- **B. 清除美股旧 IEX 数据**: 一次性 `DELETE FROM bars WHERE market='us'` (1d 4389 + 60m 734 + 1m 8 = 5131 行)。下次访问按需重新从 SIP 拉取
- **C. 前端恢复美股 4h K 线 tab**: `apps/web/lib/intervals.ts::klineTabsForMarket` 美股可见 4h
- **D. 前端恢复美股 4h CD 信号 tab**: `detailSignalTabs` 美股可见 4h
- **E. Scheduler 重建 `cd:us:4h` cron**: ET 08:05 / 12:05 / 16:05 / 20:05 (对齐 4h bucket 收盘后 5min, 与 ET 04:00 / 08:00 / 12:00 / 16:00 / 20:00 五分隔点呼应)
- **F. CLAUDE.md 活跃约束更新**: IEX → SIP, 4h tab 美股可见

---

## 2. 非目标

- ❌ 不上 SIP 付费实时 ($99/月), 仍走 free tier + `end_safe = now - 20min` 的 15min 延迟回避
- ❌ 不动 yfinance backup 路径 (Alpaca 故障时 fallback 不变)
- ❌ 不做 ET-时钟对齐 4h bucket: 4h 重采样仍走 `_group_resample` 数组下标切 (现状)。**ET 时钟对齐作为独立 task 列入 `docs/TODO.md`,生效于所有市场 (ashare/hk/us/crypto)**, 见 §6
- ❌ 不动 Alpaca latest_quote / verify_ticker (`feed` 参数对它们无影响)
- ❌ 不预热 SIP 数据, 用户首次访问按需触发

---

## 3. 数据源决策

| 维度 | 之前 (IEX) | 现在 (SIP free + 15min 延迟) |
|---|---|---|
| 1d 历史起点 | 2020-07-27 (1462 根) | 2020-01-02 (1604 根, +142) |
| 60m 一日 bar 数 | 6-8 根 (盘中为主, prepost 残缺) | 16 根稳定 (04:00-19:00 ET 全覆盖) |
| 4h 重采样 (16÷4) | 不可用 (残缺 bucket) | 4 根/日 完整 |
| `end` 限制 | 留 20min 余量 (实测) | 必须 `end <= now - 15min`, 当前留 20min 余量 |
| 实时 latest_quote | 不受 feed 影响 | 不变 |
| Free tier 可用 | ✓ | ✓ (验证: `feed='sip', end=now-20min`) |

**关键风险**: SIP 对 `end` 时间窗口比 IEX 严格。如果用户某次请求落在"刚好 now 之前 < 15min"区间, SIP 报错 `subscription does not permit querying recent SIP data`。我们已经有 `end_safe = now - timedelta(minutes=20)` 余量, 不变。

---

## 4. 实施面

| 层 | 文件 | 改动 |
|---|---|---|
| adapter | `core/adapters/us.py` | L149 + L214 `feed='iex'` → `feed='sip'` (2 处单行改) |
| DB | 一次性 SQL | `DELETE FROM bars WHERE market='us'` (备份后) |
| 前端 | `apps/web/lib/intervals.ts` | `klineTabsForMarket` + `detailSignalTabs` 把 `allowFourH` 条件从 `market === 'crypto'` 改为 `market === 'crypto' \|\| market === 'us'` |
| scheduler | `core/scheduler/scheduler.py::attach_us_signal_jobs` | 加 `cd:us:4h` job (4 次/日 ET cron) |
| docs | `CLAUDE.md` | 活跃约束: IEX→SIP, 4h tab 美股可见 |
| docs | `docs/TODO.md` | 加 ET 时钟对齐 4h bucket TODO (跨市场) |
| tests | `tests/unit/adapters/test_us.py` | 改 2 个 IEX 测试断言为 SIP (assert `feed=='sip'`); 加 1 个 4h scheduler smoke 测试 (可选) |

### 4.1 us.py 改动 (verbatim)

```python
# _fetch_history_alpaca (L149 附近)
req = StockBarsRequest(
    symbol_or_symbols=yf_symbol,
    timeframe=TimeFrame.Day,
    start=start, end=end_safe, feed="sip",  # SIP: 全美 16 交易所; free tier 受 15min 延迟限, end_safe 已留 20min 余量
    adjustment="all",
)

# _fetch_intraday_alpaca (L214 附近)
req = StockBarsRequest(
    symbol_or_symbols=yf_symbol,
    timeframe=tf_map[freq],
    start=start, end=end_safe, feed="sip",
    adjustment="all",
)
```

### 4.2 intervals.ts 改动

```typescript
// klineTabsForMarket
const allowFourH = market === 'crypto' || market === 'us'

// detailSignalTabs
const allowFourH = market === 'crypto' || market === 'us'
```

注释同步更新: `// 4h: crypto + 美股 SIP (16 60m bars/day, 16÷4=4 根) 显示;A 股/HK 4h ≡ 1d 无意义`

### 4.3 scheduler `cd:us:4h` cron 设计

ET bucket 边界: 04:00 / 08:00 / 12:00 / 16:00 / 20:00 (5 个 4h 切分点, 4 个 4h bucket)
扫描时机: bucket 收盘后 5min → ET 08:05 / 12:05 / 16:05 / 20:05 (4 次/日)

```python
sched.add_job(
    func=scan_signal,
    trigger=CronTrigger(hour="8,12,16,20", minute=5, timezone="America/New_York"),
    id="cd:us:4h",
    kwargs={"interval": "4h", "market_filter": "us"},
    **common,
)
```

(对比 A 股 `cd:4h` 是 BJT 15:10 一次/日 因 A 股一天只有 1 根 4h。美股因 prepost 16h, 一天 4 根, 故需 4 次扫描。)

---

## 5. 故障矩阵

| 故障 | 现象 | 处理 |
|---|---|---|
| SIP 报 `subscription does not permit ... recent SIP data` | end_safe 时区 / 时钟漂移 | log warning + fallback yfinance (现有 backup_cb 路径) |
| SIP 历史窗口超过 free 限制 (理论上无, 实测 2020-至今都通) | 异常 | 同上 fallback |
| SIP 单 symbol 返空 (冷门 ticker IEX/SIP 都不交易) | resp.data 空 list | fallback yfinance |
| 4h scheduler cron 时区错 (DST 切换) | bucket 错位 ±1h | APScheduler `timezone='America/New_York'` 自动处理 DST |
| 用户在 4h tab 但当日盘前还没 04:00 ET | bars 0 条 | UI 显示"无数据"(正常, 等盘前数据) |

---

## 6. 跨市场 ET/clock 时钟对齐 4h bucket (未实施, 列入 TODO)

**当前现状**: `core/services/kline_service.py::_group_resample` 用数组下标 `for i in range(0, len(bars), group_size)` 切。当源数组起点不是 4h 边界 (比如美股盘中第一根是 09:30 ET 而非 08:00 ET 的情况), bucket 会错位。

**期望状态**: 按市场所在时区的 4h 自然刻度切 (美股 ET, A 股 BJT, HK BJT, crypto UTC)。富途 / 老虎 / TradingView 都是按时钟切 (4h 起点固定 00:00/04:00/08:00/12:00/16:00/20:00)。

**单独 task**(本期不做, 写入 `docs/TODO.md`):
- 文件: `core/services/kline_service.py::_group_resample`
- 改动: 按 `bar.ts` 的本地时区小时数 / 4 分桶, 而非按数组下标
- 影响: 所有市场 4h K 线 + 4h CD 信号 (ashare / hk / us / crypto)
- 估代价: 中 (需要市场 → 时区映射, 测试要回归 4 个市场 4h)

---

## 7. 验证

### 7.1 后端

```bash
# 1. 切 feed 后冒烟
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"

# 2. SIP 1d 实测
curl -s -m 30 "http://localhost:8787/api/symbols/AAPL/bars?interval=1d&days=2200" \
  | python -c "import json,sys; d=json.load(sys.stdin); print('bars:', len(d['bars']), 'first:', d['bars'][0]['ts'])"
# 期望: bars >= 1500, first 在 2020-01-02 附近 (而非 2020-07-27)

# 3. SIP 60m 实测 (验证 prepost 完整)
curl -s -m 30 "http://localhost:8787/api/symbols/AAPL/bars?interval=60m&days=7" \
  | python -c "import json,sys; d=json.load(sys.stdin); bars=d['bars']; print('bars:', len(bars))"
# 期望: bars ~= 16 * 5 = 80 (5 个交易日 × 16 根/日)

# 4. 4h 重采样实测
curl -s -m 30 "http://localhost:8787/api/symbols/AAPL/bars?interval=4h&days=7" \
  | python -c "import json,sys; d=json.load(sys.stdin); bars=d['bars']; print('bars:', len(bars))"
# 期望: bars ~= 4 * 5 = 20 (5 个交易日 × 4 根/日)

# 5. 4h CD 信号扫描 smoke
. .venv/bin/activate && python -c "
import asyncio
from apps.api.deps import get_signal_scan_service
async def main():
    svc = get_signal_scan_service()
    n = await svc.scan_symbol('AAPL', '4h')
    print('us 4h scan ok, new signals:', n)
asyncio.run(main())
"
# 期望: 不报错 (可能 0 条信号, 但不抛异常)
```

### 7.2 前端

- 详情页 `/symbol/AAPL` 切 4h tab → K 线显示 (≥ 20 根)
- 详情页 CDSignalPanel → 4h tab 出现, 点击不报错
- 关注页美股 tab → 4h tab 出现 (无论列表是否含 crypto)

### 7.3 单测

```bash
pytest tests/unit/adapters/test_us.py -v
# 期望: 全部通过 (含改造后的 feed='sip' 断言)
```

---

## 8. 回滚

- `core/adapters/us.py`: 2 处 `feed='sip'` → `feed='iex'` revert
- `apps/web/lib/intervals.ts`: 2 处 `allowFourH` 表达式 revert 回 `crypto only`
- `core/scheduler/scheduler.py`: 删 `cd:us:4h` job
- DB: SIP 数据保留无害 (IEX 是 SIP 子集, 数据点更少)
- `CLAUDE.md` / `docs/TODO.md`: revert
- 无 schema 改动

---

## 9. 关键约束摘要 (给实施者)

- `feed='sip'` 必传 (字符串, alpaca-py SDK ≥ 0.33 接受)
- `end_safe = now - timedelta(minutes=20)` 不变 (SIP 15min 延迟)
- `adjustment='all'` 不变 (前复权, 雷区已记)
- 一次性 `DELETE FROM bars WHERE market='us'` 必须 API 停 (DuckDB 单写者)
- 删除前备份 `data/bars.duckdb` (已有 `data/bars.duckdb.before-split-adj-2026-05-21`, 这次再加一份带 today date)
- Scheduler `cd:us:4h` 必须 `timezone='America/New_York'` (DST 自动处理)
- 4h 数组下标切 vs ET 时钟切的 bucket 错位问题已知, 列入 TODO 跨市场统一处理
