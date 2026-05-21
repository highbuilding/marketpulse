# 美股 Alpaca SIP feed 切换 + 恢复 4h tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alpaca historical / intraday 从 IEX 切到 SIP feed (free tier + 15min 延迟), 清美股旧数据按需重拉; 前端恢复美股 4h K 线 + 4h CD 信号 tab; scheduler 加 `cd:us:4h` cron。

**Architecture:**
- `_fetch_history_alpaca` / `_fetch_intraday_alpaca` 各 1 处单行: `feed='iex'` → `feed='sip'`
- 一次性 DELETE FROM bars WHERE market='us' (备份后)
- 前端 `klineTabsForMarket` / `detailSignalTabs` 把 `allowFourH` 由 crypto-only 扩到 us+crypto
- `attach_us_signal_jobs` 加 `cd:us:4h` cron (ET 08:05/12:05/16:05/20:05)
- ET 时钟对齐 4h bucket 列入 TODO (跨市场, 本期不做)

**Tech Stack:** alpaca-py、duckdb、Next.js、APScheduler、pytest

**Spec:** `docs/superpowers/specs/2026-05-21-us-alpaca-sip-and-4h-design.md`

---

## File Structure

修改:
- `core/adapters/us.py` — 2 处 `feed='iex'` → `feed='sip'`
- `tests/unit/adapters/test_us.py` — 加 2 个 feed='sip' 断言测试 (TDD)
- `apps/web/lib/intervals.ts` — 2 处 allowFourH 改 (`crypto` → `crypto`+`us`)
- `core/scheduler/scheduler.py` — `attach_us_signal_jobs` 加 `cd:us:4h` job
- 一次性 SQL: DELETE FROM bars WHERE market='us' (5131 行, 含备份步)
- `CLAUDE.md` — 活跃约束更新 (IEX→SIP, 4h 美股可见)
- `docs/TODO.md` — 加跨市场 ET 时钟对齐 4h bucket TODO

---

## Task 1: USAdapter feed iex → sip

**Files:**
- Modify: `core/adapters/us.py` (L149 + L214)
- Modify: `tests/unit/adapters/test_us.py` (加 2 个测试)

- [ ] **Step 1: 写失败测试**

把以下追加到 `tests/unit/adapters/test_us.py` 末尾:

```python
@pytest.mark.asyncio
async def test_fetch_history_alpaca_uses_sip_feed():
    """SIP feed: 全美 16 交易所聚合, 1d 历史更长 + intraday prepost 完整。
    free tier 通过 end <= now-15min 拿到 SIP 数据。
    """
    adapter = USAdapter()
    adapter.has_primary = True
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": []}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    req = fake_client.get_stock_bars.call_args.args[0]
    feed = getattr(req, "feed", None)
    assert feed is not None, "feed 参数必须显式传"
    assert str(feed).lower().endswith("sip"), f"expected 'sip', got {feed!r}"


@pytest.mark.asyncio
async def test_fetch_intraday_alpaca_uses_sip_feed():
    """intraday 也走 SIP, 拿到完整 prepost 16 60m bars/day。"""
    adapter = USAdapter()
    adapter.has_primary = True
    fake_resp = MagicMock()
    fake_resp.data = {"AAPL": []}
    fake_client = MagicMock()
    fake_client.get_stock_bars = MagicMock(return_value=fake_resp)
    with patch("alpaca.data.historical.StockHistoricalDataClient",
               return_value=fake_client):
        await adapter.fetch_intraday("AAPL", freq="60")
    req = fake_client.get_stock_bars.call_args.args[0]
    feed = getattr(req, "feed", None)
    assert feed is not None
    assert str(feed).lower().endswith("sip"), f"expected 'sip', got {feed!r}"
```

- [ ] **Step 2: 跑测试, 确认 FAIL**

```bash
cd /Users/xiangrong/stock/marketpulse
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -k "uses_sip_feed" -v
```
Expected: 2 个测试 FAIL (feed 当前是 'iex')

- [ ] **Step 3: 改 `_fetch_history_alpaca`**

Edit `core/adapters/us.py` L214:
```python
            start=start, end=end_safe, feed="iex",
```
改为:
```python
            start=start, end=end_safe, feed="sip",  # SIP: 全美 16 交易所; free tier end_safe=now-20min 余量
```

- [ ] **Step 4: 改 `_fetch_intraday_alpaca`**

Edit `core/adapters/us.py` L149:
```python
            start=start, end=end_safe, feed="iex",
```
改为:
```python
            start=start, end=end_safe, feed="sip",  # SIP: 16 60m bars/day 完整 prepost
```

- [ ] **Step 5: 跑测试, 确认 PASS**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v 2>&1 | tail -30
```
Expected: 全部通过 (原 24 + 新 2 = 26 passed)

- [ ] **Step 6: 验证 alpaca-py 接受 feed='sip' 字符串**

```bash
. .venv/bin/activate && python -c "
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime
req = StockBarsRequest(
    symbol_or_symbols='AAPL',
    timeframe=TimeFrame.Day,
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 10),
    feed='sip',
    adjustment='all',
)
print('feed:', req.feed)
print('OK')
"
```
Expected: `feed: sip` + `OK`

- [ ] **Step 7: import smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): Alpaca historical/intraday feed iex→sip

SIP 全美 16 交易所聚合, 拿 1d 历史 ~2020-01-02 起(IEX 是 2020-07-27),
60m 完整 16 根/日 prepost(IEX 6-8 根残缺)。
Free tier 通过 end_safe=now-20min 余量绕过 SIP 15min 延迟限制。"
```

---

## Task 2: 清美股旧 IEX bars

**Files:**
- 一次性 SQL 操作

- [ ] **Step 1: 确认 API 已停**

```bash
cd /Users/xiangrong/stock/marketpulse
lsof -i:8787 -sTCP:LISTEN 2>&1 | head -3
ps aux | grep "uvicorn apps.api.main:app" | grep -v grep
```
Expected: 都为空 (本会话已停)。如还在跑: `pkill -9 -f "uvicorn apps.api.main:app"; sleep 2`

- [ ] **Step 2: 备份 DuckDB**

```bash
cp data/bars.duckdb data/bars.duckdb.before-sip-2026-05-21
ls -la data/bars.duckdb*
```
Expected: 看到原 db + 新备份 (today date) + 上次的 split-adj 备份。

- [ ] **Step 3: 清前快照**

```bash
. .venv/bin/activate && python -c "
import duckdb
con = duckdb.connect('data/bars.duckdb')
total = con.execute(\"SELECT COUNT(*) FROM bars WHERE market='us'\").fetchone()[0]
print(f'us bars before delete: {total}')
rows = con.execute(\"SELECT interval, COUNT(*) FROM bars WHERE market='us' GROUP BY interval ORDER BY interval\").fetchall()
for iv, c in rows:
    print(f'  {iv}: {c}')
con.close()
"
```
Expected: `us bars before delete: ~5131` (1d 4389 + 60m 734 + 1m 8, 实际数可能略不同)

- [ ] **Step 4: 执行 DELETE**

```bash
. .venv/bin/activate && python -c "
import duckdb
con = duckdb.connect('data/bars.duckdb')
con.execute(\"DELETE FROM bars WHERE market='us'\")
con.commit()
total = con.execute(\"SELECT COUNT(*) FROM bars WHERE market='us'\").fetchone()[0]
print(f'us bars after delete: {total}')
print('非美股 bars 总数(确认未误删):')
rows = con.execute(\"SELECT market, COUNT(*) FROM bars GROUP BY market ORDER BY market\").fetchall()
for m, c in rows:
    print(f'  {m}: {c}')
con.close()
"
```
Expected:
- `us bars after delete: 0`
- A 股 / hk / crypto 数量与清前一致 (ashare ~144000+, hk ~43000, crypto ~200)

- [ ] **Step 5: Commit (空 commit, 便于回溯)**

```bash
git commit --allow-empty -m "chore(db): 清除美股 IEX bars(SIP 切换前置), 备份 data/bars.duckdb.before-sip-2026-05-21

DELETE FROM bars WHERE market='us'(~5131 行)。
下次访问任何美股 symbol 时, fetch_history/fetch_intraday 拉 SIP 数据填库。"
```

---

## Task 3: 前端 4h tab 美股可见

**Files:**
- Modify: `apps/web/lib/intervals.ts` (2 处)

- [ ] **Step 1: Read intervals.ts**

```bash
cat apps/web/lib/intervals.ts
```
确认 L34-42 (`klineTabsForMarket`) + L45-53 (`detailSignalTabs`) 当前 `allowFourH = market === 'crypto'`。

- [ ] **Step 2: 改 `klineTabsForMarket`**

Edit:
```typescript
// K 线 tab(详情页用): 4h 仅 crypto 显示(美股 Alpaca IEX prepost bar 稀疏,4h 重采样残缺;A 股/HK 4h ≡ 1d 无意义)
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  // 4h 仅 crypto 显示(美股 Alpaca IEX prepost bar 稀疏, 4h 重采样残缺;A 股/HK 4h ≡ 1d 无意义)
  const allowFourH = market === 'crypto'
```
改为:
```typescript
// K 线 tab(详情页用): 4h 仅 crypto + 美股 SIP 显示(美股 SIP 16 60m bars/day 完整 prepost, 4h 重采样 4 根/日;A 股/HK 4h ≡ 1d 无意义)
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  // 4h: crypto + 美股 SIP(16 60m bars/day → 4 根/日);A 股/HK 4h ≡ 1d 无意义
  const allowFourH = market === 'crypto' || market === 'us'
```

- [ ] **Step 3: 改 `detailSignalTabs`**

Edit:
```typescript
// 详情页 CDSignalPanel tab: 4h 仅 crypto 显示
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  // 4h 仅 crypto 显示
  const allowFourH = market === 'crypto'
```
改为:
```typescript
// 详情页 CDSignalPanel tab: 4h 仅 crypto + 美股显示
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  // 4h: crypto + 美股(对齐 K 线 tab 可见性)
  const allowFourH = market === 'crypto' || market === 'us'
```

- [ ] **Step 4: 前端类型检查**

```bash
cd apps/web && npx tsc --noEmit 2>&1 | tail -10
```
Expected: 无 error

- [ ] **Step 5: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/lib/intervals.ts
git commit -m "feat(web): 美股 4h K 线 + CD 信号 tab 恢复

SIP feed 拿到 16 60m bars/day 完整 prepost,
4h 重采样 4 根/日(08:00/12:00/16:00/20:00 ET 边界),tab 恢复条件成熟。"
```

---

## Task 4: Scheduler `cd:us:4h` cron

**Files:**
- Modify: `core/scheduler/scheduler.py::attach_us_signal_jobs`

- [ ] **Step 1: Read scheduler.py L123-172**

确认 `attach_us_signal_jobs` 当前 4 个 job (15m/30m/60m/1d), 无 4h。

- [ ] **Step 2: 加 `cd:us:4h` cron**

在 `core/scheduler/scheduler.py::attach_us_signal_jobs` 中, `cd:us:60m` job 后(约 L161 后) + `cd:us:1d` job 前 插入:

```python
    # 4h: 4 个 bucket 收盘后 +5 min, ET 08:05/12:05/16:05/20:05
    # bucket 边界 ET 04/08/12/16/20 (5 个切分点, 4 根 4h/日 含完整 prepost+regular+afterhours)
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="8,12,16,20", minute="5", timezone=et),
        id="cd:us:4h",
        kwargs={"interval": "4h", "market_filter": "us"},
        **common,
    )
```

- [ ] **Step 3: import smoke + scheduler 加载**

```bash
. .venv/bin/activate && python -c "
from core.scheduler.scheduler import attach_us_signal_jobs
print('import ok')
"
```
Expected: `import ok`

- [ ] **Step 4: 模拟 attach (验证 job_id 不重复)**

```bash
. .venv/bin/activate && python -c "
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from unittest.mock import MagicMock
from core.scheduler.scheduler import attach_us_signal_jobs

sched = AsyncIOScheduler()
attach_us_signal_jobs(sched, signal_scan=MagicMock(), watchlist=MagicMock())
ids = [j.id for j in sched.get_jobs()]
print('US jobs:', sorted(ids))
assert 'cd:us:4h' in ids, 'cd:us:4h job 没注册'
print('OK')
"
```
Expected: `US jobs: ['cd:us:15m', 'cd:us:1d', 'cd:us:30m', 'cd:us:4h', 'cd:us:60m']` + `OK`

- [ ] **Step 5: Commit**

```bash
git add core/scheduler/scheduler.py
git commit -m "feat(scheduler): 加 cd:us:4h cron(ET 08:05/12:05/16:05/20:05)

4h bucket 收盘后 +5min 触发, 对齐 ET 04/08/12/16/20 5 个切分点。
依赖 SIP feed 提供 16 60m bars/day 完整 prepost+regular+afterhours。"
```

---

## Task 5: e2e 验证 + 文档 + 收尾

**Files:**
- Modify: `CLAUDE.md` (活跃约束)
- Modify: `docs/TODO.md` (加 ET 时钟对齐 4h bucket TODO)

- [ ] **Step 1: 重启 API**

```bash
cd /Users/xiangrong/stock/marketpulse
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 8
curl -s -m 3 http://localhost:8787/api/health | python -c "import sys,json; print(json.load(sys.stdin)['status'])"
```
Expected: `ok`

- [ ] **Step 2: 验证 SIP 1d 历史扩长**

```bash
curl -s -m 30 "http://localhost:8787/api/symbols/AAPL/bars?interval=1d&days=2200" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
bars = d.get('bars', [])
print('bars:', len(bars))
if bars:
    print('first:', bars[0]['ts'], 'close:', bars[0]['close'])
    print('last:', bars[-1]['ts'], 'close:', bars[-1]['close'])
"
```
Expected:
- bars >= 1500 (实测 1604, 比 IEX 1462 多 ~140)
- first 在 2020-01-02 附近 (而非 IEX 的 2020-07-27)
- last 在 2026-05-20 附近

- [ ] **Step 3: 验证 SIP 60m prepost 完整 (16 根/日)**

```bash
curl -s -m 30 "http://localhost:8787/api/symbols/AAPL/bars?interval=60m&days=7" \
  | python -c "
import json, sys
from collections import Counter
from datetime import datetime, timezone, timedelta
d = json.load(sys.stdin)
bars = d.get('bars', [])
print('bars total:', len(bars))
# 按 ET 自然日分组数 bar
et_offset = timedelta(hours=-4)  # EDT 简化(5 月已切夏令时)
by_day = Counter()
for b in bars:
    ts = datetime.fromisoformat(b['ts'].replace('Z', '+00:00'))
    et_day = (ts + et_offset).date().isoformat()
    by_day[et_day] += 1
for day in sorted(by_day):
    print(f'  {day}: {by_day[day]} bars')
"
```
Expected: 每个交易日 ≈ 16 根 (盘前 04-09:30 + 盘中 09:30-16 + 盘后 16-20, 即 hour 4-19 共 16 根)。可能首末日不全, 但中间日应稳定 16。

- [ ] **Step 4: 验证 4h 重采样**

```bash
curl -s -m 30 "http://localhost:8787/api/symbols/AAPL/bars?interval=4h&days=7" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
bars = d.get('bars', [])
print('4h bars:', len(bars))
if bars:
    print('first:', bars[0]['ts'])
    print('last:', bars[-1]['ts'])
"
```
Expected: bars ~= 4 × 5 = 20 (5 个交易日 × 4 根/日)

- [ ] **Step 5: 验证 4h CD 扫描 smoke**

```bash
. .venv/bin/activate && python -c "
import asyncio
from apps.api.deps import get_signal_scan_service
async def main():
    svc = get_signal_scan_service()
    n = await svc.scan_symbol('AAPL', '4h')
    print('us 4h scan result, new signals:', n)
asyncio.run(main())
"
```
Expected: 不抛异常 (n 可能 0, 也可能 >=1)

- [ ] **Step 6: 前端冒烟 (用户操作或 curl 模拟)**

```bash
curl -s -m 5 http://localhost:3000/symbol/AAPL > /dev/null && echo "page reachable"
```
Expected: `page reachable` (前端如未启 dev 跳过此步)

(必要时手工: 浏览器开 `http://localhost:3000/symbol/AAPL` → 切 4h tab → K 线显示 ≥ 20 根 → CDSignalPanel 4h tab 出现且不报错)

- [ ] **Step 7: 改 `CLAUDE.md` 活跃约束**

Read CLAUDE.md 找到末尾"当前活跃约束"小节。把现有 2 条美股相关行替换为:

```markdown
- 美股数据源 2026-05-21 切到 Alpaca **SIP feed**(原 IEX): 全美 16 交易所聚合,1d 历史从 2020-01-02 起(IEX 是 2020-07-27),60m 16 根/日完整 prepost。Free tier 通过 `end_safe=now-20min` 绕过 SIP 15min 延迟限。adjustment='all' 不变(前复权)。
- **美股 4h tab 已启用**(2026-05-21):前端 detail/watchlist 都可见,scheduler `cd:us:4h` ET 08:05/12:05/16:05/20:05 跑 4 次/日。4h 重采样仍走数组下标切(`_group_resample`),与 ET 时钟对齐有 bucket 错位风险,跨市场统一处理列入 `docs/TODO.md`。
```

- [ ] **Step 8: 改 `docs/TODO.md` 加跨市场 TODO**

Read TODO.md, 在合适位置(如"中价值低代价"或"已识别但未排期"区)加:

```markdown
### 4h bucket 时钟对齐(跨市场)

**当前**: `core/services/kline_service.py::_group_resample` 用数组下标 `for i in range(0, len(bars), group_size)` 切。
**期望**: 按市场所在时区的 4h 自然刻度切(美股 ET 04/08/12/16/20, A 股 BJT, HK BJT, crypto UTC)。
**影响**: 当源数组起点不在 4h 边界时(如美股盘中第 1 根是 09:30 ET 而非 08:00 ET 起点),bucket 错位 →
  - K 线显示偏移
  - CD 信号 trigger 时刻不对齐外部行情软件(富途/老虎/TradingView 都按时钟切)
**估代价**: 中(需要 market → 时区映射, 测试要回归 4 个市场的 4h)
**触发**: 美股 SIP 切换后 4h tab 启用,2026-05-21 验收时如发现 bucket 错位影响判断,优先级抬高
```

- [ ] **Step 9: 全量回归 + Commit 文档**

```bash
. .venv/bin/activate && pytest tests/unit/ -q 2>&1 | tail -10
cd apps/web && npx tsc --noEmit 2>&1 | tail -5 && cd /Users/xiangrong/stock/marketpulse
git add CLAUDE.md docs/TODO.md
git commit -m "docs: 美股 SIP + 4h tab 切换记录, 加跨市场 4h 时钟对齐 TODO"
```
Expected: 测试全过 + tsc 无 error

- [ ] **Step 10: 服务保持运行(收尾)**

```bash
lsof -i:8787 -sTCP:LISTEN 2>&1 | head -3
curl -s -m 3 http://localhost:8787/api/health | grep -o '"status":"[^"]*"'
```
Expected: 端口仍 LISTEN, status="ok"。**不要 pkill, 留服务运行让用户验收**。

---

## Self-Review

**Spec 覆盖**:
- §1 目标 A (feed iex→sip × 2 处) → Task 1 ✓
- §1 目标 B (清美股旧 bars) → Task 2 ✓
- §1 目标 C (前端 4h K 线 tab) → Task 3 ✓
- §1 目标 D (前端 4h CD 信号 tab) → Task 3 ✓
- §1 目标 E (cd:us:4h cron) → Task 4 ✓
- §1 目标 F (CLAUDE.md + TODO) → Task 5 Step 7-8 ✓
- §2 非目标:不上 SIP 付费 / 不动 yfinance backup / 不做 ET 时钟对齐(本期) — 无 task 涉及 ✓
- §3 数据源决策 → Task 5 Step 2-4 实测验证 ✓
- §4 实施面 ↔ Task 1-5 全覆盖 ✓
- §5 故障矩阵 → backup_cb 路径未动, fallback 自然继承 ✓
- §6 跨市场 ET 对齐 → Task 5 Step 8 写入 TODO ✓

**Placeholder 扫描**: 无 TBD; 每步含完整代码 / 命令 / 期望输出。

**类型一致性**: `feed='sip'` 字符串两处一致;两个测试都用 `getattr(req, 'feed')` + `str(feed).lower().endswith('sip')` 接受字符串或 enum。

**风险点**:
- alpaca-py SDK 是否接受 `feed='sip'` 字符串(Task 1 Step 6 验证)
- DELETE 必须 API 停(Task 2 Step 1 强校验)
- e2e 验证依赖 Alpaca 真实接口, 网络抖动可 retry
- 4h scheduler `timezone='America/New_York'` DST 切换由 APScheduler 处理, 无需手动改
- 前端 tsc 无 error 但视觉验收需用户开浏览器(Task 5 Step 6 备注)
