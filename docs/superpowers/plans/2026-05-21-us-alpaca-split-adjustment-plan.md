# 美股 Alpaca 前复权修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 USAdapter 的 Alpaca historical / intraday 调用都加 `adjustment='all'`(前复权),并清除 DuckDB 中所有未复权的美股 bar,首次访问按需触发拉取前复权数据。

**Architecture:**
- `_fetch_history_alpaca` / `_fetch_intraday_alpaca` 加 `adjustment='all'` 参数(2 处单行改动)
- 一次性 `DELETE FROM bars WHERE market='us'`(清除 25191 条未复权数据)
- 不预热,KLineService 现有逻辑会在用户访问时按需触发

**Tech Stack:** alpaca-py、duckdb、pytest

**Spec:** `docs/superpowers/specs/2026-05-21-us-alpaca-split-adjustment-design.md`

---

## File Structure

修改:
- `core/adapters/us.py` — 2 处加 `adjustment='all'`
- `tests/unit/adapters/test_us.py` — 新增 1 个断言测试 + 改造 1 个现有测试增加 adjustment 检查
- 一次性 SQL 操作(无文件改动,直接 duckdb CLI 跑)
- `CLAUDE.md` — 雷区或活跃约束加一行

---

## Task 1: USAdapter 加 `adjustment='all'`

**Files:**
- Modify: `core/adapters/us.py`(`_fetch_history_alpaca` + `_fetch_intraday_alpaca` 两处)
- Modify: `tests/unit/adapters/test_us.py`(加测试)

- [ ] **Step 1: 写失败测试**

把以下追加到 `tests/unit/adapters/test_us.py` 末尾:

```python
@pytest.mark.asyncio
async def test_fetch_history_alpaca_uses_adjustment_all():
    """前复权: StockBarsRequest 必须带 adjustment='all',否则 split/dividend 不平滑。"""
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
    # alpaca-py SDK 把 adjustment 字段存为字符串或 enum
    adj = getattr(req, "adjustment", None)
    assert adj is not None, "adjustment 参数必须显式传"
    # 接受 'all' 字符串 或 Adjustment.ALL enum
    assert str(adj).lower().endswith("all"), f"expected 'all', got {adj!r}"


@pytest.mark.asyncio
async def test_fetch_intraday_alpaca_uses_adjustment_all():
    """intraday 同样要前复权(尽管 60 天内 split 罕见, 保持一致)。"""
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
    adj = getattr(req, "adjustment", None)
    assert adj is not None
    assert str(adj).lower().endswith("all"), f"expected 'all', got {adj!r}"
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
cd /Users/xiangrong/stock/marketpulse
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -k "uses_adjustment_all" -v
```
Expected: 2 个测试 FAIL(`adj is None` 因为没传)

- [ ] **Step 3: 改 `_fetch_history_alpaca` 加 `adjustment='all'`**

Read `core/adapters/us.py`,找 `_fetch_history_alpaca` 中 `StockBarsRequest(...)` 那段,加 `adjustment='all'`:

```python
    req = StockBarsRequest(
        symbol_or_symbols=yf_symbol,
        timeframe=TimeFrame.Day,
        start=start, end=end_safe, feed="iex",
        adjustment="all",  # 前复权: split + dividend 都按当前股本回算
    )
```

- [ ] **Step 4: 改 `_fetch_intraday_alpaca` 加 `adjustment='all'`**

`_fetch_intraday_alpaca` 中的 `StockBarsRequest`:

```python
    req = StockBarsRequest(
        symbol_or_symbols=yf_symbol,
        timeframe=tf_map[freq],
        start=start, end=end_safe, feed="iex",
        adjustment="all",  # 前复权(intraday 60 天内 split 罕见, 保持一致)
    )
```

- [ ] **Step 5: 跑测试,确认 PASS**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v 2>&1 | tail -30
```
Expected: 全部 24 passed(原 22 + 新 2)

- [ ] **Step 6: 验证 alpaca-py 接受 adjustment='all' 字符串**

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
    feed='iex',
    adjustment='all',
)
print('adjustment:', req.adjustment)
print('OK')
"
```
Expected: `adjustment: all` + `OK`(SDK 内部会把字符串转为 Adjustment.ALL enum)

- [ ] **Step 7: import smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): Alpaca historical/intraday 加 adjustment='all' 前复权"
```

---

## Task 2: 清除 DuckDB 美股未复权数据

**Files:**
- 一次性 SQL 操作(无代码改动)

- [ ] **Step 1: 确认 API 已停**

```bash
cd /Users/xiangrong/stock/marketpulse
lsof -i:8787 -sTCP:LISTEN 2>&1 | head -3
ps aux | grep "uvicorn apps.api.main:app" | grep -v grep
```
Expected: 都为空(本会话已停)。如还在跑,执行 `pkill -9 -f "uvicorn apps.api.main:app"; sleep 2`。

(DuckDB 单写者,API 在跑时无法 DELETE)

- [ ] **Step 2: 备份 DuckDB(防误)**

```bash
cp data/bars.duckdb data/bars.duckdb.before-split-adj-2026-05-21
ls -la data/bars.duckdb*
```
Expected: 看到原 db + 备份 db。

- [ ] **Step 3: 清前快照**

```bash
. .venv/bin/activate && python -c "
import duckdb
con = duckdb.connect('data/bars.duckdb')
total = con.execute(\"SELECT COUNT(*) FROM bars WHERE market='us'\").fetchone()[0]
print(f'us bars before delete: {total}')
con.close()
"
```
Expected: `us bars before delete: 25191`(实测数,你的 db 可能略不同)。

- [ ] **Step 4: 执行 DELETE**

```bash
. .venv/bin/activate && python -c "
import duckdb
con = duckdb.connect('data/bars.duckdb')
con.execute(\"DELETE FROM bars WHERE market='us'\")
con.commit()
total = con.execute(\"SELECT COUNT(*) FROM bars WHERE market='us'\").fetchone()[0]
print(f'us bars after delete: {total}')
print(f'非美股 bars 总数(确认未误删):')
rows = con.execute(\"SELECT market, COUNT(*) FROM bars GROUP BY market ORDER BY market\").fetchall()
for m, c in rows:
    print(f'  {m}: {c}')
con.close()
"
```
Expected:
- `us bars after delete: 0`
- A 股 / hk / crypto 数量与清前一致(grep 之前总数,见 Step 3 之前的总览)

- [ ] **Step 5: Commit**(无代码改动,但写一条空 commit 记录,便于回溯)

```bash
git commit --allow-empty -m "chore(db): 清除美股未复权 bars(前复权切换前置), 备份 data/bars.duckdb.before-split-adj-2026-05-21

DELETE FROM bars WHERE market='us'(25191 行)。
下次访问任何美股 symbol 时, fetch_history/fetch_intraday 拉前复权数据填库。"
```

---

## Task 3: 重启 + e2e 验证 NVDA split 平滑

**Files:**
- Modify: `CLAUDE.md`(活跃约束加一行,见 Step 5)

- [ ] **Step 1: 重启 API**

```bash
cd /Users/xiangrong/stock/marketpulse
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
disown
sleep 8
curl -s -m 3 http://localhost:8787/api/health | python -c "import sys,json; print(json.load(sys.stdin)['status'])"
```
Expected: `ok`

- [ ] **Step 2: 触发 NVDA 全历史拉取**

```bash
curl -s -m 30 "http://localhost:8787/api/symbols/NVDA/bars?interval=1d&days=2200" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
bars = d.get('bars', [])
print('bars:', len(bars))
print('first:', bars[0]['ts'], 'close:', bars[0]['close']) if bars else None
print('last:', bars[-1]['ts'], 'close:', bars[-1]['close']) if bars else None
"
```
Expected:
- bars ≥ 1000(2020-至今,约 1462 根)
- first 在 2020-07-27 附近,close 应在 ~$10-12 之间(2020 年 NVDA 当时未拆分前 ~$400,经过 2021/06 4:1 + 2024/06 10:1 后 ÷40,即 ~$10)
- last 在 2026-05-20 附近,close ~$130(实际值)

- [ ] **Step 3: 验证 NVDA 2024-06 split 平滑**

```bash
. .venv/bin/activate && python -c "
import duckdb
con = duckdb.connect('data/bars.duckdb', read_only=True)
print('=== NVDA 2024-06-07 (split 前) → 06-10 (split 后) ===')
rows = con.execute(\"\"\"
  SELECT ts, open, close, volume
  FROM bars
  WHERE symbol='NVDA' AND interval='1d'
    AND ts BETWEEN '2024-06-05' AND '2024-06-12'
  ORDER BY ts
\"\"\").fetchall()
for ts, o, c, v in rows:
    print(f'  {ts.date()}  open={float(o):8.2f}  close={float(c):8.2f}  vol={int(v):>14,}')
print()
# 验证连续: split 前 close × 0.1(因 10:1)和 split 后 open 差值 < 5%
if len(rows) >= 2:
    pre_close = None
    post_open = None
    for ts, o, c, v in rows:
        if ts.date().isoformat() == '2024-06-07':
            pre_close = float(c)
        if ts.date().isoformat() == '2024-06-10':
            post_open = float(o)
    if pre_close and post_open:
        diff_pct = abs(post_open - pre_close) / pre_close * 100
        print(f'split 前 close: {pre_close}, split 后 open: {post_open}, diff: {diff_pct:.2f}%')
        assert diff_pct < 5.0, f'split 处仍有 {diff_pct:.2f}% 跳变, 复权未生效!'
        print('SPLIT_SMOOTH_OK')
con.close()
"
```
Expected:
- 2024-06-07 close 和 2024-06-10 open 都在 ~$120 附近,差异 < 5%
- 输出 `SPLIT_SMOOTH_OK`

- [ ] **Step 4: 验证 AAPL(也有 4:1 split 在 2020-08)同样平滑**

```bash
curl -s -m 30 "http://localhost:8787/api/symbols/AAPL/bars?interval=1d&days=2200" >/dev/null
. .venv/bin/activate && python -c "
import duckdb
con = duckdb.connect('data/bars.duckdb', read_only=True)
# AAPL 2020-08-31 4:1 split: 之前 ~\$500 → 之后 ~\$125
# 我们的窗口从 2020-07-27 开始, 起点应已是复权后的 ~\$100
rows = con.execute(\"\"\"
  SELECT ts, close FROM bars
  WHERE symbol='AAPL' AND interval='1d'
  ORDER BY ts ASC LIMIT 5
\"\"\").fetchall()
print('AAPL 最早 5 根(2020-07-27 起):')
for ts, c in rows:
    print(f'  {ts.date()}  close={float(c):.2f}')
# 复权后, 2020-07-27 close 应 < \$200(若没复权会是 ~\$380)
first_close = float(rows[0][1])
assert first_close < 200, f'AAPL 2020-07-27 close={first_close}, 未前复权!'
print(f'AAPL_PREADJ_OK (first close={first_close:.2f})')
con.close()
"
```
Expected: AAPL 2020-07-27 close < $200(实际应 ~$92,因为 4:1 split 后)+ `AAPL_PREADJ_OK`

- [ ] **Step 5: 改 `CLAUDE.md` 加活跃约束**

Read CLAUDE.md 找到"当前活跃约束"小节(在文件末尾附近)。在该小节末尾追加一行:

```markdown
- 美股 1d / intraday 走 Alpaca IEX 前复权(`adjustment='all'`),split + dividend 都已按当前股本回算。2026-05-21 修复:之前 raw 数据导致 NVDA 2024-06 split 处价格跳水,K 线 + CD 信号失真。如果 user 报"价格跳变",先检查是否在 split 日;如确实未复权,看 `core/adapters/us.py::_fetch_history_alpaca` 的 `adjustment` 参数
```

- [ ] **Step 6: 全量回归 + Commit**

```bash
. .venv/bin/activate && pytest tests/unit/ -q 2>&1 | tail -10
cd /Users/xiangrong/stock/marketpulse
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): 美股 Alpaca 前复权约束加入活跃约束"
```

---

## Self-Review

**Spec 覆盖**:
- §1 目标 a (`adjustment='all'`) → Task 1 ✓
- §1 目标 b (DELETE FROM bars where market='us') → Task 2 ✓
- §1 目标 c (不预热,按需触发) → Task 3 Step 2 触发即拉(自然按需)✓
- §2 非目标:不改 yfinance / 不加开关 / 不预热 — 无 task 涉及 ✓
- §3 实施面:adapter 2 处 + DELETE + 1 个测试 + CLAUDE.md ↔ Task 1/2/3 全覆盖 ✓
- §4 验证(NVDA 2024-06 split 平滑) → Task 3 Step 3 ✓

**Placeholder 扫描**:无 TBD;每步 + 完整代码 / 命令 / 期望输出。

**类型一致性**:`adjustment='all'` 字符串在两处一致;两个测试都用 `getattr(req, 'adjustment')` + `str(adj).lower().endswith('all')` 接受字符串或 enum。

**风险点**:
- alpaca-py SDK 版本 ≥ 0.33 应该接受 `adjustment='all'` 字符串(verify in Task 1 Step 6)
- DELETE 走 duckdb,API 必须先停(Task 2 Step 1 强校验)
- e2e 验证依赖 Alpaca 真实接口,如临时网络抖动 Task 3 验证可能 retry 一次
