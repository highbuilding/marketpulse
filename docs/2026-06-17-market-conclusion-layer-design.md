# MarketPulse 结论层设计:事实流水标准化与盘后复盘工程化

状态日期:2026-06-17  
目标:吸收 Gemini 白皮书中“盘后定方向、盘中定触发、风险网闸、特征矩阵”的工程思想,去掉实盘交易执行和 AI 决策,把 MarketPulse 当前零散事实流水压缩成可解释的结论层。

---

## 1. 总体边界

### 1.1 做什么

- 盘中信息聚合:把 `live_messages`、`theme_snapshots`、后续涨停/炸板/盘口异动事实表,聚合成“市场状态 / 题材状态 / 风险状态 / 观察池”。
- 每日复盘结论:收盘后生成当日市场结构、题材强弱、连板生态、亏钱效应、次日观察方向。
- 统计公式标准化:每个结论必须带 `score`、`label`、`evidence`、`formula_version`。
- 数据源分层:盘中只用稳定/低成本源;盘后允许使用较慢或不稳定的低频接口。

### 1.2 不做什么

- 不接 QMT / Mini-QMT / 自动下单。
- 不生成“买入/卖出指令”,只生成“观察/风险/等待确认/剔除”。
- 不让 LLM 或 AI 直接读取原始 K 线/Tick 做判断。
- 不把东财/同花顺实时接口作为盘中硬依赖;失败必须优雅降级。

---

## 2. 目标架构

```text
盘中事实源
  live_messages
  theme_snapshots
  indicator_signals
  quote/bars cache
  limit_pool_intraday(新增)
  stock_changes_intraday(新增)

        ↓ 统计公式

结论层 API
  /api/conclusions/intraday
  /api/conclusions/daily-review
  /api/conclusions/candidates

        ↓ 展示

盘中结论面板
每日复盘页
低位容量趋势观察池
```

第一阶段先不新增持久化表,直接从现有 `live_messages + theme_snapshots` 生成实时结论。第二阶段把 AkShare 涨停池等接口接入事实表。第三阶段收盘后写入每日复盘表。

---

## 3. 数据源分层

### 3.1 盘中需要获取的数据源

| 数据 | 接口/事实源 | 频率 | 用途 | 风险 |
|---|---|---:|---|---|
| A 股实时 quote | sina HTTP `hq.sinajs.cn` | 10s | 指数/采集清单宽度、题材快照、自选异动 | 稳定,现有主源 |
| 5m/15m/30m K 线收线 | `stock_zh_a_minute` 经 `ak_call` | 收线触发 | 5m 放量、CD 信号、盘中趋势 | sina 限频/空体 |
| 题材快照 | `theme_snapshots` | quote tick 驱动 | 题材扩散、分歧、成交集中度 | 当前为采集清单代理 |
| 实盘消息 | `live_messages` | 事件驱动 | 事实流水、风险/信号聚合 | 消息多且散,需压缩 |
| 涨停股池 | `stock_zt_pool_em` | 5-15min | 真实涨停数、连板数、封板资金、炸板次数 | 东财源,需低频+降级 |
| 炸板股池 | `stock_zt_pool_zbgc_em` | 5-15min | 真实炸板池、炸板率、退潮风险 | 仅近 30 交易日 |
| 跌停股池 | `stock_zt_pool_dtgc_em` | 5-15min | 跌停风险、系统性亏钱效应 | 仅近 30 交易日 |
| 盘口异动 | `stock_changes_em` | 1-5min | 大笔买入/卖出、封涨停、打开涨停 | 不能逐条刷屏,只统计 |
| 板块异动 | `stock_board_change_em` | 5-15min | 板块异动次数、主力净流入 | 东财源,低频使用 |

盘中新增接口必须先补 `core/integrations/akshare.py` source mapping,把 `stock_zt_pool_*`、`stock_changes_em`、`stock_board_change_em`、`stock_lhb_*` 映射到 `em`,避免限频/熔断错误归入 sina。

### 3.2 低频接口,盘后获取即可

| 数据 | 接口 | 频率 | 用途 |
|---|---|---:|---|
| 昨日涨停股池 | `stock_zt_pool_previous_em` | 次日开盘/盘后 | 昨日涨停溢价、晋级率、淘汰惩罚 |
| 龙虎榜详情 | `stock_lhb_detail_em` | 18:00 后 | 资金性质评分、席位参与度 |
| 龙虎榜个股统计 | `stock_lhb_stock_statistic_em` | 每日/每周 | 个股短线资金活跃度 |
| 个股公告 | `stock_notice_report` / `stock_individual_notice_report` | 盘后 | 异动原因、风险提示 |
| 估值/市值 | `stock_zh_valuation_baidu` | 每日/每周 | 总市值、估值背景 |
| 个股信息 | `stock_individual_info_em` | 每日/手动 | 流通市值/行业/上市信息;当前探测不稳 |
| 同花顺资金流 | `stock_fund_flow_individual/concept/industry` | 盘后或低频 | 核心股/题材资金确认 |
| 筹码分布 | `stock_cyq_em` | 盘后 | 筹码集中度、获利盘比例 |

---

## 4. 标准化公式

### 4.1 盘中消息聚合公式

输入:`live_messages` 最近窗口。

```text
level_weight = info:1, watch:2, warning:3, critical:5
category_weight = risk:1.25, theme:1.15, signal:1.05, index:1.0, watchlist:1.0

message_pressure(category) =
  Σ(level_weight(message.level) * category_weight(category))

risk_pressure =
  message_pressure(risk) + 0.5 * message_pressure(theme_warning_or_critical)

signal_balance =
  count(CD买入信号) - count(CD卖出信号)
```

结论标签:

- `risk_pressure >= 12`:风险升高
- `risk_pressure >= 6`:风险偏高
- 否则:风险可控

### 4.2 题材结论公式

输入:`theme_snapshots` 最近窗口,按题材取最新快照和窗口内前后变化。

```text
theme_heat_score =
  40 * clamp(up_ratio, 0, 1)
  + 20 * clamp(pct_change / 5, -1, 1)
  + 15 * clamp(pct_change_5m / 2, -1, 1)
  + 15 * clamp(amount_ratio - 1, 0, 2) / 2
  + 10 * clamp(limit_up_count / 3, 0, 1)
  - 15 * clamp(divergence_score / 100, 0, 1)

theme_momentum =
  latest.up_ratio - first.up_ratio
```

结论标签:

- `theme_heat_score >= 70 且 theme_momentum >= 0`:主线扩散
- `theme_heat_score >= 55`:题材活跃
- `theme_momentum <= -0.2`:题材回落
- 否则:题材观察

### 4.3 涨停/炸板/连板公式

输入:新增 `limit_pool_intraday/daily`。

```text
break_rate =
  broken_count / max(limit_up_count + broken_count, 1)

max_ladder_height =
  max(limit_pool.连板数)

ladder_break_count =
  count(h in 1..max_ladder_height where ladder_count[h] == 0)

seal_quality =
  median(封板资金 / max(成交额, 1))

limit_mood_score =
  30 * clamp(limit_up_count / 80, 0, 1)
  - 35 * clamp(break_rate / 0.45, 0, 1)
  - 20 * clamp(down_limit_count / 30, 0, 1)
  + 15 * clamp(max_ladder_height / 7, 0, 1)
```

### 4.4 昨日涨停表现公式

输入:`stock_zt_pool_previous_em` + 当日涨停池/quote。

```text
previous_limit_open_edge =
  mean(today_open / yesterday_close - 1)

previous_limit_current_edge =
  mean(current_price / yesterday_close - 1)

promotion_rate =
  count(yesterday_limit ∩ today_limit) / count(yesterday_limit)

loser_penalty =
  mean(change_pct of yesterday_limit but not today_limit)
```

### 4.5 低位容量趋势观察池公式

输入:日线 K 线、题材快照、5m 放量、资金流/涨停结构。

```text
position_ratio =
  (close - min(close, 20d)) / max(max(close, 20d) - min(close, 20d), eps)

ma20_fit =
  close / ma20

volume_spike_5m =
  current_5m_volume / max(mean(last_n_5m_volume_same_window), eps)

capacity_score =
  score(free_float_cap in [50亿, 200亿])
  + score(turnover_5d_avg >= 5亿)

low_position_score =
  score(position_ratio in [0.1, 0.4])
  + score(ma20_fit in [1.00, 1.05])

theme_confirm_score =
  score(theme_label in 主线扩散/题材活跃)
  + score(theme_amount_ratio >= 1.2)

candidate_score =
  capacity_score
  + low_position_score
  + theme_confirm_score
  + score(volume_spike_5m >= 2)
  + score(main_net > 0)
  - score(break_rate >= 0.4)
  - score(risk_pressure >= 12)
```

输出决策只能是:

- `observe`:进入观察
- `wait_confirm`:等待确认
- `risk_hold`:风险未解除
- `exclude`:剔除

---

## 5. 新增数据表建议

### 5.1 `limit_pool_daily`

保存每日涨停/炸板/跌停事实。

字段:

- `market`
- `trade_date`
- `pool_type`: `limit_up` / `broken_limit` / `down_limit`
- `symbol`
- `name`
- `change_pct`
- `price`
- `amount`
- `free_float_cap`
- `total_cap`
- `turnover_rate`
- `seal_amount`
- `first_seal_time`
- `last_seal_time`
- `break_count`
- `ladder_count`
- `industry`
- `raw_json`

### 5.2 `market_conclusions`

保存盘中/盘后结论快照。

字段:

- `market`
- `conclusion_key`
- `scope`: `intraday` / `daily`
- `ts`
- `title`
- `label`
- `score`
- `summary`
- `evidence_json`
- `formula_version`

### 5.3 `daily_reviews`

保存每日复盘。

字段:

- `market`
- `trade_date`
- `market_label`
- `risk_label`
- `theme_label`
- `limit_label`
- `candidate_count`
- `summary_json`
- `evidence_json`
- `generated_at`

### 5.4 `watch_candidates`

保存观察池,替代当前 `trade_candidates` 的交易语义。

字段:

- `market`
- `trade_date`
- `candidate_key`
- `symbol`
- `name`
- `candidate_type`: `low_position_capacity_trend` 等
- `decision`: `observe` / `wait_confirm` / `risk_hold` / `exclude`
- `score`
- `reasons_json`
- `risks_json`
- `evidence_json`
- `status`

---

## 6. API 设计

### 6.1 盘中结论

```http
GET /api/conclusions/intraday?market=ashare&minutes=60
```

返回:

- `market_state`:市场脉搏、消息压力、风险压力
- `theme_state`:主线题材、回落题材、题材热度
- `risk_state`:风险标签、风险证据
- `signal_state`:CD 信号买卖平衡
- `sections`:可直接展示的结论卡片
- `data_gaps`:缺失数据提示

### 6.2 每日复盘

```http
GET /api/conclusions/daily-review?market=ashare&date=YYYY-MM-DD
```

第一版可从 `live_messages + theme_snapshots + limit_pool_daily` 即时生成;后续由 collector 收盘后写 `daily_reviews`。

### 6.3 观察池

```http
GET /api/conclusions/candidates?market=ashare&date=YYYY-MM-DD
```

只返回观察结论和证据,不返回交易指令。

---

## 7. 落地阶段

### Phase 1:当前事实源结论层

- 新增 `MarketConclusionService`
- 新增 `/api/conclusions/intraday`
- 只读 `live_messages + theme_snapshots`
- 前端助手从消息列表升级为结论层展示

### Phase 2:AkShare 短线结构事实表

- 补 `akshare._infer_source` 映射:
  - `stock_zt_pool_*` → `em`
  - `stock_changes_em` → `em`
  - `stock_board_change_em` → `em`
  - `stock_lhb_*` → `em`
- 新增 `LimitPoolService/Repo`
- collector 盘中低频拉涨停/炸板/跌停池
- 收盘后定稿 `limit_pool_daily`

### Phase 3:每日复盘

- 新增 `DailyReviewService`
- 收盘后生成 `daily_reviews`
- `/replay` 页面升级为“回放 + 复盘结论”

### Phase 4:低位容量趋势观察池

- 新增 `WatchCandidateService/Repo`
- 从 K 线、题材、涨停结构、资金流生成观察池
- 页面展示理由链和风险条件

---

## 8. 验收口径

- `/api/conclusions/intraday` 在无涨停池数据时仍能返回结论,并在 `data_gaps` 标注缺口。
- 盘中页面不再直接平铺最近几十条消息作为主结论。
- 每条结论都包含公式证据,可以追溯到事实源。
- 新 AkShare 接口只能由 collector/service 调用,API route 不直接打 AkShare。
- 东财接口失败不能导致 API 502,只能降级为“短线结构数据缺失”。
