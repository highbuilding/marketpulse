# A 股 AI 盘中决策助手 — 技术设计文档

> 日期:2026-06-07 · 状态:已确认，进入实施  
> 范围:围绕 `/market` 和 `/assistant`，把现有数据底座升级为“行情事实层 + AI 决策结论层”。  
> 原型: `docs/prototypes/ai-assistant-demo.html`

---

## 0. 关键结论

本期不是做自动交易，也不是让 AI 直接下单。本期目标是:

```text
程序生成事实、指标、事件和候选
AI 基于结构化证据输出明确交易观察结论
用户人工决定是否下单
```

产品上拆成两层:

```text
/market     = 原始盘面 + 结构化指标 + 事实事件流
/assistant  = AI 盘面结论 + 开单观察 + 持仓建议 + 风险提醒
```

已确认实施口径:

```text
持仓删除: 软删除 status='closed'
AI provider: 后端配置驱动，不做前端模型配置 UI
通知: 实现内部通知候选/模板/冷却/状态，不接外部主动推送渠道
题材扫描: collector 独立 job，默认 180 秒，配置化，API/Web 不触发采集
```

## 1. 强制边界

### 1.1 UI 集成边界

正式接入时只允许改:

```text
apps/web/app/market/page.tsx
apps/web/app/assistant/page.tsx
必要时新增 market/assistant 专用组件
必要时新增 market/assistant 专用 API client/types
```

不得修改:

```text
apps/web/app/layout.tsx
Sidebar
TopBar
MarketSwitcher
全局主题
现有 /symbol
现有 /watchlist
现有 /signals
现有 /strategy
现有 /trading
现有设置页
```

原型里的横向市场按钮只用于表达信息架构。正式接入必须复用线上已有的顶部市场下拉。

### 1.2 市场支持边界

本期完整实现:

```text
market = ashare
```

其他市场本期不接 AI 决策:

```text
US / HK / Crypto:
  /market     展示精简事实层
  /assistant  展示 unsupported empty state
```

但架构必须 market-aware，不能写死 A 股类名或表结构。其他市场通过 provider 扩展。

### 1.3 进程边界

延续当前项目架构:

- `collector/ashare` 允许调用 AKShare、写 DB、写 Redis、生成题材快照/事件/候选。
- `apps/api` 只读 Redis / SQLite / collector 内部只读接口，不直接打 AKShare，不直连 DuckDB。
- Redis 作为热缓存和 bus，SQLite 存事件/候选/AI 结论/持仓，DuckDB 保留历史 K 线和分时。

## 2. 当前可复用能力

现有能力:

- `core.services.market_query.MarketQueryService`
  - `sectors_ashare()`
  - `sector_constituents_ashare(sector_code)`
  - A 股行业/概念统一经 `ak_call`
- `core.services.ai_market_service.AIMarketService`
  - 已能生成 A 股 AI market packet
  - 有指数、宽度、热门/弱势板块、自选股、规则事件
- `core.market_rules.*`
  - 市场宽度、指数风格、板块扩散、个股异动规则雏形
- `core.services.volume_indicator_service.VolumeIndicatorService`
  - 已有量能指标
- `core.services.chip_service.ChipService`
  - 已有日线筹码摘要
- `core.persistence.signal_repo.SignalRepo`
  - 已有 CD 信号落库
- `core.notifications.*`
  - 已有邮件/通知基础

本期不是重做这些能力，而是在其上新增:

```text
ThemeRadar
ThemeState
MarketEvent
TradeCandidate
Position
AITradeOpinion
```

## 3. 产品信息架构

### 3.1 `/market`: 事实层

`/market` 负责回答:

```text
现在市场发生了什么?
哪个题材强?
哪个题材低位启动?
板块内部是否扩散?
分歧是否放大?
核心股和后排怎么走?
持仓股有哪些事实风险?
```

A 股模块:

1. 大盘脉搏
   - 上证、深成、创业板、沪深300、中证1000、科创50
   - 涨跌家数
   - 全 A 成交额
   - 风格强弱: 成长/权重、小票/大票

2. 题材 / 板块雷达
   - 热门题材
   - 低位启动题材
   - 退潮题材
   - 状态: `WARMING / HOT / ACCELERATING / DIVERGING / REPAIRING / FADING`
   - 指标: 涨幅、5m、上涨占比、成交放大、分歧评分、承接评分

3. 题材详情
   - 成分股列表
   - 角色: 龙头 / 核心 / 中军 / 跟风 / 杂毛
   - 个股涨幅、成交额、量比、是否站上分时均线

4. 个股异动
   - 全 A 快速涨幅/跌幅
   - 放量突破
   - 跌破分时均线
   - 自选/持仓异动

5. 事实事件流
   - 程序生成的 market events
   - 不放 AI 长解释，只放事实和证据

美股精简事实层:

- ETF/指数代理: SPY / QQQ / DIA / IWM
- Mega Cap / 核心股
- 自选异动
- 盘前盘后状态
- 事件流

Crypto 精简事实层:

- BTC / ETH / SOL 概览
- 主流币排行
- 波动/风险状态
- 事件流

HK 本期保守展示已接入的指数/自选/事件空态，不做题材逻辑。

### 3.2 `/assistant`: 结论层

`/assistant` 负责回答:

```text
现在该关注什么?
哪个题材值得开单观察?
哪个题材不能追?
我持仓应该继续拿、减仓观察、离场观察还是加仓观察?
风险在哪里?
为什么?
```

A 股模块:

1. 今日 AI 盘面结论
   - 积极试错 / 谨慎观察 / 防守
   - 主线是谁
   - 是否出现题材切换
   - 主要风险

2. AI 开单观察
   - `open_watch`
   - `wait_pullback`
   - `observe_only`
   - `avoid`

3. AI 热门题材研判
   - 例如: 机器人弱分歧，有承接，等核心回踩
   - 例如: AI 手机高位加速，不追后排

4. AI 持仓建议
   - `hold`
   - `add_watch`
   - `reduce_watch`
   - `exit_watch`
   - `risk_alert`

5. AI 追问
   - 基于当前结构化证据回答
   - 不从用户问题里直接触发外部数据源调用

非 A 股:

```text
AI 助手当前仅支持 A 股题材决策。
其他市场后续单独设计。
```

## 4. 数据模型

### 4.1 `positions`

手动维护持仓，第一版不接券商。

```sql
CREATE TABLE positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT,
  quantity INTEGER DEFAULT 0,
  cost_price REAL,
  opened_at TEXT,
  strategy_tag TEXT,
  entry_reason TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(market, symbol)
);
```

第一版 UI 支持新增、编辑、删除/标记清仓。为保留复盘，删除建议实现为 `status='closed'`。

### 4.2 `theme_snapshots`

题材快照，只保存板块级摘要，不保存全量原始分钟行情。

```sql
CREATE TABLE theme_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  ts TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  theme_name TEXT NOT NULL,
  theme_type TEXT NOT NULL,
  change_pct REAL,
  change_5m REAL,
  change_15m REAL,
  amount REAL,
  amount_ratio REAL,
  up_count INTEGER,
  down_count INTEGER,
  up_ratio REAL,
  median_change_pct REAL,
  top10_avg_change_pct REAL,
  leader_symbol TEXT,
  leader_name TEXT,
  leader_change_pct REAL,
  leader_dominance_pct REAL,
  created_at TEXT NOT NULL
);
```

### 4.3 `theme_states`

当前题材状态。

```sql
CREATE TABLE theme_states (
  market TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  theme_name TEXT NOT NULL,
  state TEXT NOT NULL,
  previous_state TEXT,
  score REAL,
  updated_at TEXT NOT NULL,
  reason_json TEXT NOT NULL,
  PRIMARY KEY(market, theme_code)
);
```

状态枚举:

```text
COLD
WARMING
HOT
ACCELERATING
DIVERGING
REPAIRING
FADING
SWITCHING
```

### 4.4 `theme_memberships`

股票到题材的本地映射。每次拉成分股时更新。

```sql
CREATE TABLE theme_memberships (
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  theme_name TEXT NOT NULL,
  theme_type TEXT NOT NULL,
  role TEXT,
  score REAL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY(market, symbol, theme_code)
);
```

### 4.5 `market_events`

事实事件，AI 前置输入之一。

```sql
CREATE TABLE market_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  ts TEXT NOT NULL,
  category TEXT NOT NULL,
  event_type TEXT NOT NULL,
  level TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT,
  target_type TEXT,
  target_id TEXT,
  symbols_json TEXT,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

### 4.6 `trade_candidates`

程序筛出的 AI 分析候选。

```sql
CREATE TABLE trade_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  ts TEXT NOT NULL,
  candidate_type TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  symbol TEXT,
  theme_code TEXT,
  priority TEXT NOT NULL,
  score REAL,
  source_event_ids_json TEXT,
  evidence_json TEXT NOT NULL,
  risk_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
```

候选类型:

```text
low_theme_start
hot_theme_follow
weak_divergence_buy
core_repair_buy
rotation_candidate
position_hold_review
position_risk_alert
```

### 4.7 `ai_trade_opinions`

AI 输出的明确结论。

```sql
CREATE TABLE ai_trade_opinions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  candidate_id INTEGER,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  symbol TEXT,
  theme_code TEXT,
  decision TEXT NOT NULL,
  confidence REAL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  risks_json TEXT NOT NULL,
  watch_points_json TEXT NOT NULL,
  invalidation_json TEXT NOT NULL,
  model TEXT,
  prompt_hash TEXT,
  created_at TEXT NOT NULL
);
```

决策枚举:

```text
open_watch
wait_pullback
observe_only
avoid
hold
add_watch
reduce_watch
exit_watch
risk_alert
```

## 5. 后端模块设计

### 5.1 Theme provider

市场扩展通过 provider，不把 A 股写死到主服务。

```python
class ThemeProvider(Protocol):
    market: Market

    async def fetch_theme_snapshots(self) -> list[ThemeSnapshotInput]: ...
    async def fetch_constituents(self, theme_code: str) -> list[ThemeConstituentInput]: ...
    async def resolve_symbol_themes(self, symbols: list[str]) -> dict[str, list[ThemeRef]]: ...
```

本期实现:

```text
AShareThemeProvider
  - MarketQueryService.sectors_ashare()
  - MarketQueryService.sector_constituents_ashare()
  - industry + concept
```

暂不支持:

```text
UnsupportedThemeProvider for us/hk/crypto
```

### 5.2 ThemeRadarService

职责:

1. 拉 A 股行业/概念快照。
2. 选出候选题材集合。
3. 拉候选题材成分股。
4. 计算题材指标。
5. 写 `theme_snapshots` 和 `theme_memberships`。

候选题材集合第一版:

```text
涨幅 Top 20
成交额 Top 20
跌幅 Bottom 10
最近 30 分钟活跃题材
自选股/持仓所属题材
```

不做每分钟全量成分股扫描。

### 5.3 ThemeStateEngine

职责:

根据最近 N 次 `theme_snapshots` 更新 `theme_states`。

规则第一版:

```text
WARMING:
  近期非持续热门
  change_5m > 0.8%
  amount_ratio > 1.5
  up_ratio > 0.6
  leader_dominance_pct 不过高

HOT:
  涨幅排名前 10
  成交额排名前 20
  up_ratio > 0.6
  median_change_pct > 0

ACCELERATING:
  HOT 且 change_5m 继续抬升
  amount_ratio > 1.5
  核心股增强

DIVERGING:
  板块仍在前排
  up_ratio 10 分钟下降超过 20%
  median_change_pct 回落
  leader_dominance_pct 升高

REPAIRING:
  前一状态 DIVERGING
  核心股重新站上分时均线
  up_ratio 回升
  amount 未明显萎缩

FADING:
  排名快速下滑
  up_ratio < 0.45
  核心股跌破分时均线
```

### 5.4 RoleResolver

职责:

给成分股标注:

```text
leader
core
mid_core
follower
laggard
junk
```

第一版规则:

```text
leader:
  板块内涨幅前 3
  成交额前 5
  5m 强于板块
  多次作为领涨股出现

core:
  涨幅前 10
  成交额前 10
  量能放大
  站上分时均线

mid_core:
  成交额大
  涨幅不一定最高
  波动相对稳定

follower:
  跟随上涨
  成交额一般
  无主动领涨

junk:
  成交额靠后
  低于板块中位数
  量比异常但涨幅不跟
  板块分歧/退潮时回落更快
```

注意: 核心/杂毛由程序评分，不交给 AI 自由判断。

### 5.5 MarketEventEngine

职责:

把事实变化落成 `market_events`。

事件类型:

```text
theme_hot_rank_up
theme_volume_expansion
theme_diffusion_up
theme_divergence_expand
theme_chip_loose
theme_support_seen
theme_repair_confirm
theme_fading
theme_rotation
symbol_volume_breakout
symbol_break_intraday_avg
position_risk_expand
market_width_deteriorate
```

### 5.6 CandidateEngine

职责:

把 `theme_states + market_events + positions` 合成为 `trade_candidates`。

规则:

```text
low_theme_start:
  state = WARMING
  amount_ratio > 1.5
  up_ratio > 0.6
  leader_dominance_pct 不过高
  至少 2 个 core/mid_core 放量

weak_divergence_buy:
  state = DIVERGING
  主线排名仍前 10
  核心股未跌破分时均线
  后排分化但核心承接存在

core_repair_buy:
  state = REPAIRING
  core/mid_core 重新放量站回均线

position_risk_alert:
  持仓股跌破分时均线
  所属题材 DIVERGING/FADING
  个股放量走弱
```

### 5.7 AITradeOpinionService

职责:

消费 `trade_candidates`，生成 `ai_trade_opinions`。

AI 输入必须是结构化 JSON，不允许把全量原始数据直接塞给模型。

输出必须按 JSON schema:

```json
{
  "decision": "wait_pullback",
  "confidence": 0.74,
  "title": "机器人板块弱分歧，有承接，核心股等回踩",
  "summary": "板块仍是盘中主线，但后排分化扩大，不适合追后排。",
  "reasons": [],
  "risks": [],
  "watch_points": [],
  "invalidation": []
}
```

如果 LLM 失败:

- 不阻塞 collector。
- candidate 保持 pending 或标记 failed。
- 记录 warning。

## 6. API 设计

### 6.1 Positions

```text
GET    /api/positions?market=ashare
POST   /api/positions
PATCH  /api/positions/{id}
DELETE /api/positions/{id}
```

第一版仅允许 `market=ashare` 创建 AI 相关持仓。其他市场可返回 400 或 unsupported。

### 6.2 Market facts

```text
GET /api/themes/radar?market=ashare
GET /api/themes/{theme_code}?market=ashare
GET /api/market-events?market=ashare&limit=100
```

非 A 股:

```json
{
  "supported": false,
  "market": "us",
  "reason": "theme radar unsupported for this market"
}
```

### 6.3 Assistant

```text
GET  /api/assistant/dashboard?market=ashare
GET  /api/assistant/opinions?market=ashare
GET  /api/assistant/opinions/{id}
POST /api/assistant/chat
```

非 A 股:

```json
{
  "supported": false,
  "market": "us",
  "reason": "AI 助手当前仅支持 A 股题材决策"
}
```

## 7. 前端接入设计

### 7.1 `/market`

读取现有全局 market context，不改全局下拉。

按 market 渲染:

```text
ashare:
  大盘脉搏
  题材雷达
  题材详情
  个股异动
  事实事件流

us:
  ETF/指数概览
  Mega Cap / 核心股
  自选异动
  事件流

crypto:
  BTC/ETH/SOL 概览
  主流币排行
  波动/风险状态
  事件流

hk:
  指数/自选/基础事件空态
```

### 7.2 `/assistant`

读取现有全局 market context。

```text
ashare:
  今日 AI 盘面结论
  AI 开单观察
  AI 热门题材研判
  AI 持仓建议
  AI 追问

non-ashare:
  unsupported empty state
```

## 8. Notification

本期需要实现“可通知能力”，但第一版不接外部主动推送渠道。

具体边界:

```text
实现:
  AI 结论生成通知候选
  通知模板
  通知冷却
  通知状态记录
  后续渠道适配接口

暂不接入:
  邮件主动推送
  飞书主动推送
  企业微信主动推送
  AI 发现机会后自动发到外部 IM
```

也就是说，系统内部要知道“哪些 AI 结论值得通知”，并把通知候选准备好；但 MVP 页面稳定前，不把这些候选真正发送到外部渠道。

通知候选只覆盖高价值 AI 结论:

```text
open_watch
wait_pullback
add_watch
reduce_watch
exit_watch
risk_alert
```

冷却:

```text
same market + target_id + decision 30 分钟只通知一次
状态变化可再次通知
risk_alert 可提高优先级
```

外部渠道接入时，只新增 channel adapter，不改 AITradeOpinionService / CandidateEngine 的判断逻辑。

## 9. Non-goals

本期不做:

- 自动下单
- 券商接入
- Qlib
- backtrader
- 复杂机器学习
- 非 A 股 AI 决策
- 全量每分钟原始行情写 SQLite
- 修改全局 Layout/MarketSwitcher

## 10. 验收标准

产品验收:

- `/market` 与 `/assistant` 分工清晰，不重复堆信息。
- A 股 `/market` 能看到题材状态、成分股角色、事实事件。
- A 股 `/assistant` 能看到明确 AI 结论和持仓建议。
- 非 A 股 `/assistant` 明确展示暂不支持。
- 手动持仓支持新增、编辑、删除/清仓。

工程验收:

- API 进程不新增任何 AKShare 调用。
- 新增 AKShare 需求只经 `MarketQueryService` / `ak_call`。
- 新表全部带 `market` 字段。
- 只改 `/market`、`/assistant` 和必要支撑组件/API。
- 不改全局市场下拉。
- 后端 import 测试通过。
- 前端 `npx tsc --noEmit` 通过。
