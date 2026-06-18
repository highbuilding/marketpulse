# MarketPulse 交易大脑、复盘因子与回测闭环

更新日期: 2026-06-18

本文是当前交易研究系统的关键文档。目标是说明 MarketPulse 如何从外部数据采集出发,把盘中事实、盘后复盘、技术信号、涨停生态、候选池和 vectorbt 回测串成一个可验证的交易研究闭环。

当前版本只覆盖 A 股。不自动下单,不使用 AI 生成交易建议。系统输出的交易建议只允许作为纸面指令进入沙盒观察。

系统级总览、接口地图、K 线周期和数据流入口见:

- [system_architecture_overview.md](/Users/xiangrong/stock/marketpulse/docs/system_architecture_overview.md)

## 1. 总体链路

```text
外部数据源
  -> collector 采集任务
  -> DuckDB / SQLite 本地事实表
  -> 事实标准化和复盘因子
  -> 盘中结论 / 每日复盘 / 候选池
  -> 策略条件矩阵
  -> vectorbt 回测
  -> 沙盒准入判断
  -> 纸面交易指令
  -> /strategy 页面展示
```

核心原则:

- API 请求只读本地事实和结果,不在请求时调用外部源。
- AkShare 只能通过 `core/integrations/akshare.py::ak_call` 调用。
- K 线写入 DuckDB,状态、信号、复盘、候选池和回测结果写入 SQLite。
- 回测读取结构化字段,不读取复盘文本。
- 任何数据缺口必须写入 `data_gaps`,不能把缺数据伪装成交易结论。

## 2. 进程和职责

### API 进程

入口:

- `apps/api/main.py`

职责:

- 提供 `/api/strategy/*`、`/api/conclusions/*`、`/api/markets/*` 等读接口。
- 读取 SQLite 中已经生成的结论、候选池、回测报告和纸面指令。
- 不直接持有 DuckDB `BarRepo`。
- 不在策略接口里重跑回测。

策略相关路由:

- `apps/api/routes/strategy.py`
  - `GET /api/strategy/backtests`
  - `GET /api/strategy/backtests/{strategy_id}`
  - `GET /api/strategy/instructions`

### A 股 collector 进程

入口:

- `apps/collector/ashare/main.py`

职责:

- 持有 A 股 DuckDB `BarRepo`。
- 拉取 A 股行情、K 线、涨停池、题材、资金、CD 信号等事实。
- 定时生成候选池、低频事实、策略回测和纸面指令。
- 当前策略回测任务在收盘后运行:
  - 16:05
  - 18:30

关键任务:

- `apps/collector/jobs/watch_candidates.py`:低位容量趋势观察池。
- `apps/collector/jobs/strategy_backtests.py`:策略回测和纸面指令刷新。
- `apps/collector/jobs/lowfreq_facts.py`:龙虎榜、公告、低频资金事实采集。
- `apps/collector/jobs/limit_pool.py`:涨停池、炸板池、跌停池、昨日涨停表现池。

### 前端进程

入口:

- `apps/web`

策略页面:

- `apps/web/app/strategy/page.tsx`
- `apps/web/app/strategy/[id]/page.tsx`
- `apps/web/lib/strategy_api.ts`

职责:

- 展示真实回测结果,不再展示假数据。
- 展示资金曲线、收益分布、市场状态表现、题材表现、卖出规则对比、交易明细和数据缺口。
- 展示当前纸面指令。没有策略通过沙盒门槛时,指令列表为空。

## 3. 数据存储边界

### DuckDB

文件:

- `data/bars_ashare.duckdb`

用途:

- A 股历史 K 线。
- 回测读取 1d 日线构造价格矩阵。

关键实现:

- `core/persistence/duckdb_repo.py::BarRepo`
- `BarRepo.fetch_history_frame(...)`

API 进程不直接读 DuckDB,collector/offline 才持有 `BarRepo`。

### SQLite

文件:

- `data/state.db`

用途:

- 状态类、信号类、复盘类、候选池、回测结果。

关键表:

| 表 | 作用 |
|---|---|
| `indicator_signals` | CD 买点/卖点等技术信号 |
| `theme_snapshots` | 题材快照、热度、上涨比例、成交额变化 |
| `live_messages` | 盘中事实流水和结论事件 |
| `limit_pool_daily` | 涨停池、炸板池、跌停池、昨日涨停今日表现 |
| `trade_candidates` | 低位容量趋势观察池 |
| `lowfreq_facts_daily` 等 | 龙虎榜、公告、低频资金事实 |
| `strategy_backtest_runs` | 策略回测摘要 |
| `strategy_backtest_trades` | 策略回测交易明细 |
| `trade_instructions` | 纸面交易指令 |

Schema 位置:

- `core/persistence/schema.sql`

## 4. 采集层

采集层目标是把外部事实落到本地,不直接输出交易判断。

### K 线

来源:

- A 股历史/分钟数据通过 AShare adapter 和 AkShare。

落库:

- DuckDB `bars_ashare.duckdb`

用途:

- 技术指标。
- 低位容量趋势候选。
- 放量突破。
- vectorbt 回测价格矩阵。

### 涨停生态

来源:

- AkShare 东方财富涨停池相关接口,统一通过 `ak_call`。

落库:

- SQLite `limit_pool_daily`

池类型:

- `limit_up`:涨停池。
- `broken_limit`:炸板池。
- `down_limit`:跌停池。
- `previous`:昨日涨停今日表现池。

关键 repo:

- `core/persistence/limit_pool_repo.py`

当前可计算字段:

- `limit_up_count`:涨停数。
- `broken_count`:炸板数。
- `down_limit_count`:跌停数。
- `break_rate`:炸板率。
- `max_ladder_height`:最高连板。
- `ladder_counts`:各连板高度分布。
- `second_plus_count`:二板以上个股数。
- `third_plus_count`:三板以上个股数。
- `first_board_rate`:首板占比。
- `ladder_continuity`:梯队连续度。
- `ladder_strength_score`:连板生态强度。
- `promotion_rate`:昨日涨停晋级率。
- `red_rate`:昨日涨停今日红盘率。
- `loser_penalty_pct`:昨日涨停淘汰惩罚。
- `high_promotion_rate`:昨日二板以上高位票晋级率。
- `high_loser_penalty_pct`:昨日二板以上高位票淘汰惩罚。

当前重要数据缺口:

- 本地 `limit_pool_daily` 历史覆盖很短,当前只有 2 天真实涨停生态历史。
- 因此长周期回测中,连板生态只对有数据日期生效,其他日期退化为宽基上涨家数兜底。

### 题材快照和盘中消息

落库:

- `theme_snapshots`
- `live_messages`

用途:

- 盘中结论。
- 每日复盘。
- 题材升温策略。
- 交易大脑证据。

当前限制:

- 题材快照历史不足,导致“低位容量趋势 + 题材升温”回测交易数非常少。

### 技术信号

落库:

- `indicator_signals`

当前主要使用:

- 1d CD 买点。
- 1d CD 卖点。

用途:

- 策略触发条件。
- 盘中/复盘信号统计。

### 低频事实

落库:

- 龙虎榜。
- 公告。
- 低频资金流。

关键实现:

- `core/services/lowfreq_fact_service.py`
- `core/persistence/lowfreq_fact_repo.py`
- `apps/collector/jobs/lowfreq_facts.py`

当前定位:

- 进入每日复盘和数据缺口提示。
- 尚未作为硬交易门控。

## 5. 事实标准化层

事实标准化层把原始数据转成可回测字段。

### 市场状态

实现:

- `core/persistence/limit_pool_repo.py::market_state_window`
- `core/services/strategy_backtest_service.py::_build_feature_matrices`

结构化字段:

- `break_rate`
- `down_limit_count`
- `promotion_rate`
- `red_rate`
- `loser_penalty_pct`
- `high_promotion_rate`
- `high_loser_penalty_pct`
- `ladder_strength_score`
- `second_plus_count`
- `ladder_continuity`
- `short_term_allowed`
- `trend_allowed`
- `risk_on`
- `repair`
- `neutral`
- `risk_off`

市场状态公式摘要:

```text
risk_off =
  炸板率 >= 45%
  OR 跌停数 >= 20
  OR 昨板淘汰惩罚 <= -5%
  OR 高位淘汰惩罚 <= -6%
  OR 连板生态坍塌且炸板偏高
  OR 宽基下跌且上涨家数 <= 35%

risk_on =
  晋级率 / 涨停数 / 上涨家数达标
  AND 连板强度或二板以上广度达标
  AND 炸板 / 跌停 / 淘汰惩罚不过线

repair =
  非 risk_on / risk_off
  AND 晋级率 >= 20% 或上涨家数 >= 55%
```

如果涨停池历史缺失:

- `risk_off` 仍可由宽基下跌和上涨家数兜底。
- `risk_on` 可由宽基上涨和上涨家数兜底。
- 报告写入 `data_gaps`,说明连板/昨板生态历史覆盖不足。

### 个股状态

实现:

- `core/services/strategy_backtest_service.py::_build_feature_matrices`

结构化字段:

- `low_position`:低位容量趋势基础条件。
- `quality_low_position`:更严格的低位容量趋势条件。
- `relative_strength20`:20 日相对强度。
- `breakout`:放量突破 20 日平台。
- `sell_risk`:跌破 10 日线或单日大跌。
- `cd_buy`:CD 买点。
- `cd_sell`:CD 卖点。
- `theme_hot`:所属题材升温。

低位容量趋势第一版口径:

```text
position = (close - 120日低点) / (120日高点 - 120日低点)
low_position =
  position <= 0.35
  AND 20日成交额均值 >= 3000万
  AND close >= 20日均线 * 0.96

quality_low_position =
  low_position
  AND close >= 20日均线 * 0.98
  AND close >= 5日均线 * 0.97
  AND 20日相对强度 >= -10%
  AND 20日平均日内波动 <= 12%
```

### 题材状态

实现:

- `core/services/strategy_backtest_service.py::_theme_hot_matrix`
- `core/services/market_conclusion_service.py::_rank_themes`

结构化字段:

- `up_ratio`
- `pct_change`
- `pct_change_5m`
- `amount_ratio`
- `limit_up_count`
- `divergence_score`
- `theme_hot`

当前限制:

- 历史题材成分和快照不足,回测样本不够。

## 6. 盘中结论和每日复盘

实现:

- `core/services/market_conclusion_service.py`

API:

- `/api/conclusions/intraday`
- `/api/conclusions/daily-review`
- `/api/conclusions/candidates`

盘中结论输入:

- `live_messages`
- `theme_snapshots`
- `limit_pool_daily`

每日复盘输入:

- 当日盘中事实。
- 题材快照。
- 涨停生态。
- 昨日涨停表现。
- 低频事实。
- 日线走势 section。

输出:

- `market_state`
- `theme_state`
- `limit_structure`
- `previous_limit_performance`
- `lowfreq_facts`
- `risk_state`
- `signal_state`
- `next_watch`
- `data_gaps`

注意:

- 复盘文本是给人看的。
- 回测和交易大脑读取结构化 evidence 字段,不解析自然语言。

## 7. 候选池

实现:

- `core/services/watch_candidate_service.py`
- `core/persistence/candidate_repo.py`
- `apps/collector/jobs/watch_candidates.py`

目标:

- 把“低位容量趋势观察池”落成可查询候选池,不是只写在复盘文字里。

输入:

- 日线 K 线。
- 成交额。
- 题材状态。
- 涨停生态风险。
- 资金流和低频事实。

输出:

- `trade_candidates`

用途:

- `/api/conclusions/candidates`
- 纸面指令生成时的标的来源。

当前限制:

- 历史候选池尚未完整回填。
- 回测仍主要用 K 线结构代理历史候选条件。

## 8. 策略层

实现:

- `core/services/strategy_backtest_service.py`

当前 6 个策略:

| strategy_id | 策略 | 买入条件摘要 | 卖出条件摘要 |
|---|---|---|---|
| `low_position_cd` | 低位容量趋势 + CD 买点 | 高质量低位容量趋势 + 1d CD 买点 + 非 risk_off | CD 卖点 / 跌破均线 / 风险恶化 |
| `low_position_breakout` | 低位容量趋势 + 放量突破 | 高质量低位容量趋势 + 放量突破 + 非 risk_off | 跌破均线 / 风险恶化 |
| `low_position_theme` | 低位容量趋势 + 题材升温 | 高质量低位容量趋势 + 题材升温 + risk_on/repair | 题材退潮 / 跌破均线 / 风险恶化 |
| `cd_market_repair` | CD 买点 + 市场风险解除 | CD 买点 + risk_on/repair | CD 卖点 / 风险恶化 |
| `previous_limit_strong` | 昨日涨停强环境短线 | 昨日涨停池 + 晋级率/红盘率/高位晋级/连板强度达标 | 短线门控转弱 |
| `risk_filter` | 禁止交易/风险过滤 | 高质量低位/CD/突破原始买点 + 非 risk_off | 风险恶化 / 技术卖出 |

统一执行假设:

- 日线/盘后信号顺延 1 个交易日执行。
- 最短持有 1 日近似 T+1。
- 手续费 0.05%。
- 滑点 0.1%。
- 单笔止损默认 -6%。
- 单笔止盈默认 +12%。

## 9. vectorbt 回测层

vectorbt 的角色:

- 只负责矩阵化回测计算。
- 不理解复盘文本。
- 不负责 A 股业务规则。
- 不负责生成交易建议。

MarketPulse 负责:

- 从 DuckDB 读取日线。
- 从 SQLite 读取信号、涨停生态、题材、候选池。
- 构造价格矩阵、买入矩阵、卖出矩阵。
- 做信号顺延,避免未来函数。
- 做 T+1 近似、手续费、滑点、止损止盈。
- 生成交易明细、收益分布、市场状态归因和数据缺口。

回测结果落库:

- `strategy_backtest_runs`
- `strategy_backtest_trades`

回测输出字段:

- 样本区间。
- 股票池数量。
- 交易次数。
- 胜率。
- 平均收益。
- 中位数收益。
- 最大回撤。
- 最差单笔。
- 平均持仓天数。
- 1/3/5/10 日收益分布。
- 按年份表现。
- 按市场状态表现。
- 按题材状态表现。
- 卖出规则对比。
- 资金曲线。
- 数据缺口。

## 10. 沙盒指令层

实现:

- `core/services/strategy_backtest_service.py::_generate_instructions`
- `core/persistence/strategy_backtest_repo.py`

落库:

- `trade_instructions`

动作类型:

- `BUY_SETUP`:提前埋伏观察。
- `BUY_TRIGGER`:触发买入条件。
- `SELL_RISK`:卖出风险。
- `WAIT_CONFIRM`:等待确认。
- `AVOID`:回避。
- `HOLD`:继续持有/观察。

当前实际行为:

- 只有策略通过沙盒门槛后才生成纸面指令。
- 每轮回测先把旧 active 指令过期。
- 如果没有策略通过,指令列表为空。

沙盒准入门槛:

```text
交易数 >= 80
AND 胜率 >= 53%
AND 平均收益 >= 2.5%
AND 中位数收益 > 0
AND 最大回撤 > -18%
AND 最新年度平均收益非负
```

当前没有策略通过沙盒门槛。

## 11. 当前回测状态

最近一次真实回测:

- 市场:A 股。
- 样本:约 720 天。
- 股票池:120 只。
- 价格:1d 日线。
- 执行:信号次日执行。
- 指令:0 条。

结果:

| 策略 | 状态 | 交易数 | 胜率 | 均值收益 | 中位数 | 最大回撤 | 沙盒 |
|---|---:|---:|---:|---:|---:|---:|---|
| CD 买点 + 市场风险解除 | 观察 | 346 | 47.98% | 0.75% | -0.09% | -23.55% | 否 |
| 禁止交易/风险过滤 | 观察 | 236 | 54.24% | 1.57% | 0.19% | -17.48% | 否 |
| 低位容量趋势 + 放量突破 | 观察 | 162 | 55.56% | 1.72% | 0.45% | -14.97% | 否 |
| 低位容量趋势 + CD 买点 | 观察 | 77 | 50.65% | 1.20% | 0.05% | -12.95% | 否 |
| 低位容量趋势 + 题材升温 | 数据不足 | 3 | 33.33% | -0.06% | -1.21% | -5.21% | 否 |
| 昨日涨停强环境短线 | 数据不足 | 0 | - | - | - | 0.00% | 否 |

解释:

- 放量突破和风险过滤已有正向迹象,但平均收益不够,且近期稳定性不足。
- CD 买点样本多,但胜率、中位数、回撤都不够。
- 题材升温和昨日涨停强环境短线主要受数据不足影响。
- 当前系统应该输出“观察/回避/等待确认”,不应该输出可执行买入指令。

## 12. 当前数据缺口

最关键缺口:

1. `limit_pool_daily` 历史太短。
   - 当前只有 2 天真实涨停生态历史。
   - 连板生态和昨日涨停表现无法长期回测。

2. 题材快照历史不足。
   - `low_position_theme` 样本只有 3 笔。
   - 暂不能判断题材升温是否能提高胜率。

3. 候选池历史不足。
   - 当前能生成实时候选池。
   - 历史回测仍主要用 K 线代理低位容量趋势。

4. 盘中炸板/回封细节不足。
   - 当前有日级炸板率。
   - 尚缺回封时间、封单变化、开板次数、炸板后回封质量。

5. 交易执行约束仍是近似。
   - 涨停买不到、跌停卖不出、集合竞价滑点尚未完全建模。

## 13. 关键 API

策略:

```text
GET /api/strategy/backtests
GET /api/strategy/backtests/{strategy_id}
GET /api/strategy/instructions
```

结论:

```text
GET /api/conclusions/intraday
GET /api/conclusions/daily-review
GET /api/conclusions/candidates
```

行情变化:

```text
GET /api/markets/ashare/changes/limit-pool
GET /api/markets/ashare/changes/limit-pool/summary
```

## 14. 验证命令

后端导入:

```bash
. .venv/bin/activate && python -c "from apps.api.main import app"
```

单元测试:

```bash
. .venv/bin/activate && pytest tests/unit -q
```

前端类型检查:

```bash
cd apps/web && npx tsc --noEmit
```

AkShare 收口检查:

```bash
grep -rn --include="*.py" "^import akshare\|^from akshare" core apps tests
```

vectorbt 最小检查:

```bash
. .venv/bin/activate && python - <<'PY'
import pandas as pd
import vectorbt as vbt
close = pd.DataFrame({"a": [10, 11, 12], "b": [20, 19, 21]})
entries = pd.DataFrame({"a": [True, False, False], "b": [False, True, False]})
exits = pd.DataFrame({"a": [False, False, True], "b": [False, False, True]})
pf = vbt.Portfolio.from_signals(close, entries, exits)
print(pf.total_return())
PY
```

## 15. 下一步优先级

第一优先级:补历史事实。

- 回填或持续累积 `limit_pool_daily`。
- 回填题材快照和题材成分。
- 回填历史候选池。

第二优先级:提高执行真实性。

- 建模涨停不可买。
- 建模跌停不可卖。
- 建模次日开盘/收盘不同执行价。
- 建模集合竞价和盘中触发。

第三优先级:扩展策略。

- 强主线低吸。
- 分歧转一致。
- 昨日涨停弱转强。
- 连板梯队修复。
- 风险日空仓收益对比。

第四优先级:纸面指令追踪。

- 指令触发后跟踪 1/3/5/10 日表现。
- 统计指令命中率。
- 区分 `BUY_SETUP` 和 `BUY_TRIGGER` 的真实转化率。

## 16. 当前判断

系统已经具备交易研究闭环:

```text
采集 -> 事实 -> 复盘因子 -> 策略条件 -> 回测 -> 沙盒准入 -> 纸面指令
```

但系统还不具备稳定发出买入指令的条件。当前最可靠的价值是:

- 用炸板、跌停、昨板淘汰惩罚过滤风险日。
- 用低位容量趋势和放量突破筛出观察对象。
- 用回测结果约束交易冲动,没有统计优势就不生成指令。

在涨停生态、题材历史和候选池历史补足前,交易大脑应继续处于“观察和风险过滤”阶段。
