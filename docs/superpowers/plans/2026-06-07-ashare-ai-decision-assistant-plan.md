# A 股 AI 盘中决策助手 — 实施计划

> **状态:** 已确认，进入实施  
> **Spec:** `docs/superpowers/specs/2026-06-07-ashare-ai-decision-assistant-design.md`  
> **Prototype:** `docs/prototypes/ai-assistant-demo.html`

---

## 0. 执行原则

本计划分阶段实施。每阶段完成后跑验证，不跨阶段大改。

硬约束:

- 只围绕 `/market` 和 `/assistant` 接入。
- 不修改 `apps/web/app/layout.tsx`、全局市场下拉、Sidebar、TopBar。
- 本期 AI 决策只支持 A 股。
- 所有 schema / service / API 设计必须带 `market`，为其他市场预留扩展。
- API 进程不打 AKShare，不直连 DuckDB。
- A 股题材数据经 `MarketQueryService` 和现有 `ak_call` 链路。
- 不做自动下单。

## 1. 目标交付拆分

### Phase 0: 文档与原型确认

**状态:** 当前阶段

产物:

- `docs/prototypes/ai-assistant-demo.html`
- `docs/superpowers/specs/2026-06-07-ashare-ai-decision-assistant-design.md`
- `docs/superpowers/plans/2026-06-07-ashare-ai-decision-assistant-plan.md`

验收:

- 用户确认 `/market` 是事实层。
- 用户确认 `/assistant` 是结论层。
- 用户确认非 A 股 AI 助手暂不支持。
- 用户确认不改全局市场下拉。

### Phase 1: SQLite schema + repo 基础

目的:

先补关系数据底座，不接 AI，不改页面。

新增/修改:

```text
core/domain/models.py
core/persistence/sqlite_repo.py
core/persistence/position_repo.py
core/persistence/theme_repo.py
core/persistence/market_event_repo.py
core/persistence/candidate_repo.py
core/persistence/ai_opinion_repo.py
tests/unit/persistence/test_position_repo.py
tests/unit/persistence/test_theme_repo.py
tests/unit/persistence/test_market_event_repo.py
tests/unit/persistence/test_candidate_repo.py
tests/unit/persistence/test_ai_opinion_repo.py
```

新增表:

```text
positions
theme_snapshots
theme_states
theme_memberships
market_events
trade_candidates
ai_trade_opinions
```

实施细节:

- 表初始化放入现有 SQLite schema 初始化流程。
- 所有表带 `market`。
- repo 方法保持纯 DB 读写，不做业务判断。
- JSON 字段在 repo 层做 `json.dumps/json.loads`，route/service 不拼字符串。

验证:

```bash
. .venv/bin/activate && python -c "from apps.api.main import app"
pytest tests/unit/persistence/test_position_repo.py -q
pytest tests/unit/persistence/test_theme_repo.py -q
pytest tests/unit/persistence/test_market_event_repo.py -q
pytest tests/unit/persistence/test_candidate_repo.py -q
pytest tests/unit/persistence/test_ai_opinion_repo.py -q
```

验收:

- repo upsert/list 幂等。
- JSON 字段读写正常。
- 非 A 股 market 值可以存储，但不会被本期 worker 使用。

### Phase 2: 持仓 API + UI

目的:

先让用户可以在 UI 手动维护 A 股持仓，作为持仓建议的输入。

新增/修改:

```text
core/positions/service.py
apps/api/routes/positions.py
apps/api/deps.py
apps/api/main.py
apps/web/app/assistant/page.tsx
apps/web/lib/positions_api.ts
apps/web/lib/types.ts
```

API:

```text
GET    /api/positions?market=ashare
POST   /api/positions
PATCH  /api/positions/{id}
DELETE /api/positions/{id}
```

UI 范围:

- 只在 `/assistant` 内新增“持仓与 AI 建议”的持仓管理区域。
- 不改 `/trading`。
- 支持新增、编辑、删除/清仓。
- 非 A 股 market 时，持仓 AI 建议区域展示 unsupported。

验证:

```bash
. .venv/bin/activate && python -c "from apps.api.main import app"
pytest tests/unit -q
cd apps/web && npx tsc --noEmit
```

浏览器走查:

- `/assistant` A 股下新增持仓。
- 修改成本/买入理由。
- 删除或清仓。
- 切换非 A 股后 AI 助手展示不支持。

验收:

- 用户能手动录入持仓。
- 后端 `positions` 表有数据。
- 不影响全局布局和市场下拉。

### Phase 3: Theme Provider + ThemeRadar

目的:

在 collector 侧生成 A 股题材快照和股票-题材映射。

新增/修改:

```text
core/themes/models.py
core/themes/provider.py
core/themes/providers/ashare.py
core/themes/providers/unsupported.py
core/themes/radar_service.py
core/themes/role_resolver.py
apps/collector/jobs/theme_radar.py
apps/collector/ashare/main.py
tests/unit/themes/test_role_resolver.py
tests/unit/themes/test_radar_service.py
```

数据来源:

```text
MarketQueryService.sectors_ashare()
MarketQueryService.sector_constituents_ashare(sector_code)
Redis quote cache
可选: 已有 bars/current tail 用于是否站上分时均线
```

扫描策略:

```text
每 1-3 分钟拉行业/概念列表
候选题材集合:
  涨幅 Top 20
  成交额 Top 20
  跌幅 Bottom 10
  最近 30 分钟活跃题材
  自选股/持仓所属题材
只对候选题材拉成分股
```

输出:

```text
theme_snapshots
theme_memberships
```

RoleResolver 输出:

```text
leader / core / mid_core / follower / laggard / junk
```

验证:

```bash
. .venv/bin/activate && python -c "from apps.collector.ashare.main import app"
pytest tests/unit/themes/test_role_resolver.py -q
pytest tests/unit/themes/test_radar_service.py -q
```

手动冒烟:

```bash
# 可在 collector 环境手动跑一次 theme radar job
. .venv/bin/activate && python -m apps.collector.jobs.theme_radar --once
```

验收:

- 可以生成 A 股 theme snapshots。
- 每个候选题材有成分股角色。
- 非 A 股 provider 返回 unsupported，不触发外部调用。

### Phase 4: ThemeState + MarketEvent

目的:

把题材快照转成状态和事实事件。

新增/修改:

```text
core/themes/state_engine.py
core/market_events/models.py
core/market_events/engine.py
apps/collector/jobs/theme_state_job.py 或并入 theme_radar.py
tests/unit/themes/test_state_engine.py
tests/unit/market_events/test_market_event_engine.py
```

状态:

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

事件:

```text
theme_hot_rank_up
theme_volume_expansion
theme_diffusion_up
theme_divergence_expand
theme_support_seen
theme_repair_confirm
theme_fading
theme_rotation
market_width_deteriorate
```

输出:

```text
theme_states
market_events
```

验证:

```bash
pytest tests/unit/themes/test_state_engine.py -q
pytest tests/unit/market_events/test_market_event_engine.py -q
```

验收:

- 给定固定 snapshots，状态机结果稳定。
- 分歧放大、承接出现、退潮等事件能按规则产生。
- 事件不会重复刷屏，repo 层或 engine 层有去重窗口。

### Phase 5: CandidateEngine

目的:

把题材状态、事件和持仓组合成 AI 需要分析的候选。

新增/修改:

```text
core/themes/candidate_engine.py
apps/collector/jobs/candidate_scan.py
tests/unit/themes/test_candidate_engine.py
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

输出:

```text
trade_candidates
```

规则示例:

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

position_risk_alert:
  持仓股跌破分时均线
  所属题材 DIVERGING/FADING
  个股放量走弱
```

验证:

```bash
pytest tests/unit/themes/test_candidate_engine.py -q
```

验收:

- 固定输入能生成预期 candidate。
- 重复快照不会重复生成相同 candidate。
- 持仓风险 candidate 能带上持仓成本、盈亏、买入理由。

### Phase 6: AITradeOpinionService

目的:

AI 对候选输出明确结论。

新增/修改:

```text
core/ai/llm_client.py
core/ai/prompt_builder.py
core/ai/trade_opinion_service.py
apps/collector/jobs/ai_opinion_worker.py
tests/unit/ai/test_prompt_builder.py
tests/unit/ai/test_trade_opinion_service.py
```

配置:

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
LLM_TIMEOUT_S
```

第一版默认 OpenAI-compatible client。可以接 DeepSeek/OpenAI/Ollama 兼容接口。

输出 JSON schema:

```json
{
  "decision": "wait_pullback",
  "confidence": 0.74,
  "title": "...",
  "summary": "...",
  "reasons": [],
  "risks": [],
  "watch_points": [],
  "invalidation": []
}
```

失败策略:

- LLM 失败不影响采集。
- 写 warning 日志。
- candidate 标记 pending/failed，后续可重试。

验证:

```bash
pytest tests/unit/ai/test_prompt_builder.py -q
pytest tests/unit/ai/test_trade_opinion_service.py -q
```

验收:

- prompt 不包含无关原始大数据。
- 输出 JSON schema 校验稳定。
- 非 A 股 candidate 不调用 LLM。

### Phase 7: API read model

目的:

为 `/market` 和 `/assistant` 提供只读聚合接口。

新增/修改:

```text
apps/api/routes/themes.py
apps/api/routes/market_events.py
apps/api/routes/assistant.py
apps/api/main.py
apps/api/deps.py
tests/integration/test_api_ai_assistant.py
```

API:

```text
GET /api/themes/radar?market=ashare
GET /api/themes/{theme_code}?market=ashare
GET /api/market-events?market=ashare&limit=100
GET /api/assistant/dashboard?market=ashare
GET /api/assistant/opinions?market=ashare
GET /api/assistant/opinions/{id}
POST /api/assistant/chat
```

非 A 股返回:

```json
{
  "supported": false,
  "market": "us",
  "reason": "AI 助手当前仅支持 A 股题材决策"
}
```

验证:

```bash
. .venv/bin/activate && python -c "from apps.api.main import app"
pytest tests/integration/test_api_ai_assistant.py -q
```

验收:

- API 只读 SQLite/Redis。
- `grep -rn "ak_call" apps/api` 不新增命中。
- 非 A 股 unsupported 行为稳定。

### Phase 8: `/market` UI 接入

目的:

把 `/market` 改成按市场展示事实层。

修改范围:

```text
apps/web/app/market/page.tsx
apps/web/components/MarketPulsePanel.tsx 可选
apps/web/components/RealtimeSectorsPanel.tsx 可选
apps/web/components/ThemeRadarPanel.tsx 新增
apps/web/components/ThemeDetailPanel.tsx 新增
apps/web/components/MarketEventsPanel.tsx 新增
apps/web/lib/market_ai_api.ts 新增或命名更清晰
apps/web/lib/types.ts
```

注意:

- 不改 `layout.tsx`。
- 不改顶部市场下拉。
- A 股展示题材雷达和事件流。
- 美股/Crypto/HK 展示精简事实层或现有页面内容，不强套 A 股题材。

验证:

```bash
cd apps/web && npx tsc --noEmit
```

浏览器走查:

- A 股 `/market`: 大盘、题材雷达、题材详情、事件流。
- 美股 `/market`: ETF/核心股/事件精简视图。
- Crypto `/market`: 主流币/风险状态/事件精简视图。
- 切换市场仍使用原有下拉。

### Phase 9: `/assistant` UI 接入

目的:

把 `/assistant` 从假聊天改成 AI 结论层。

修改范围:

```text
apps/web/app/assistant/page.tsx
apps/web/components/AIMarketSummary.tsx 新增
apps/web/components/AITradeOpinions.tsx 新增
apps/web/components/AIPositionOpinions.tsx 新增
apps/web/components/AssistantChatPanel.tsx 新增
apps/web/components/PositionManagerPanel.tsx 新增
apps/web/lib/assistant_api.ts 新增
apps/web/lib/positions_api.ts
apps/web/lib/types.ts
```

行为:

- A 股展示 AI 盘面结论、开单观察、持仓建议、追问。
- 非 A 股展示 unsupported empty state。
- 支持手动新增/编辑/删除持仓。

验证:

```bash
cd apps/web && npx tsc --noEmit
```

浏览器走查:

- A 股 `/assistant` 显示结论。
- 非 A 股 `/assistant` 显示不支持。
- 新增/编辑/删除持仓。
- 追问输入能走 API 或第一版返回受控提示。

### Phase 10: 通知能力骨架

目的:

实现高优先级 AI 结论的“可通知能力”，但 MVP 不接外部主动推送渠道。

边界:

```text
本期实现:
  通知候选生成
  通知模板
  通知冷却
  通知状态记录
  channel adapter 接口

本期暂不接:
  邮件发送
  飞书发送
  企业微信发送
  AI 发现机会后自动推送到外部 IM
```

新增/修改:

```text
core/services/notification_service.py
core/notifications/templates.py
core/notifications/channels/base.py
core/persistence/notification_repo.py
apps/collector/jobs/ai_opinion_worker.py 或单独 notify worker
tests/unit/notifications/test_ai_opinion_templates.py
tests/unit/notifications/test_notification_cooldown.py
```

通知 decision:

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
same market + target_id + decision 30 分钟一次
risk_alert 可更高优先级
```

验证:

```bash
pytest tests/unit/notifications/test_ai_opinion_templates.py -q
pytest tests/unit/notifications/test_notification_cooldown.py -q
```

验收:

- 高价值 AI 结论可生成 notification candidate。
- 通知文案包含结论、理由、风险、观察点。
- 不重复刷屏。
- 外部渠道 disabled 时不发送，但状态可查询、可后续接 channel adapter。

## 2. 最小可行版本建议

为了减少一次性改动，建议 MVP 执行到 Phase 10 的“通知能力骨架”，但不接外部主动推送渠道。

MVP 必须有:

```text
positions
theme snapshots/states
market events
trade candidates
ai trade opinions
notification candidates/cooldown/templates
/market fact layer
/assistant conclusion layer
```

MVP 可以暂缓:

```text
AI chat 真问答
外部通知渠道接入
更复杂的题材切换模型
盘后复盘
回测/Qlib
```

## 3. 全量验证清单

每批实现后至少跑:

```bash
. .venv/bin/activate && python -c "from apps.api.main import app"
cd apps/web && npx tsc --noEmit
```

涉及 collector 后:

```bash
. .venv/bin/activate && python -c "from apps.collector.ashare.main import app"
```

启动验证按项目 CLAUDE.md:

```bash
docker compose -f docker-compose.dev.yml up -d redis
nohup bash -c '. .venv/bin/activate && python -m apps.collector.ashare.main' >> /tmp/collector_ashare.log 2>&1 &
nohup bash -c '. .venv/bin/activate && python -m apps.collector.us.main' >> /tmp/collector_us.log 2>&1 &
nohup bash -c '. .venv/bin/activate && python -m apps.collector.crypto.main' >> /tmp/collector_crypto.log 2>&1 &
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 &
sleep 8
curl -s -m 3 http://localhost:8787/api/health
```

日志检查:

```bash
tail -100 data/logs/api-errors.log
tail -100 data/logs/collector-errors.log
tail -50 data/logs/fault.log
```

## 4. Confirmed Decisions

用户已确认:

1. 持仓删除软删除为 `status=closed`。
2. AI provider 第一版后端配置驱动，不做前端模型配置 UI。
3. AI chat 第一版围绕已有 opinion 做受控问答，后续再扩展真自由问答。
4. 通知能力骨架纳入 MVP；外部渠道发送暂不接。
5. 题材扫描属于 collector 能力，默认 180 秒，配置化，API/Web 不触发采集。

## 5. Commit 策略

建议按阶段拆 commit:

```text
docs: 增加 A 股 AI 决策助手 spec 和 plan
feat(ai-assistant): 增加持仓与事件 schema
feat(ai-assistant): 增加 A 股题材雷达
feat(ai-assistant): 增加题材状态和候选生成
feat(ai-assistant): 增加 AI 交易观点服务
feat(web): 接入行情事实层
feat(web): 接入 AI 助手结论层
feat(notification): 接入 AI 观点通知
```

不 push，除非用户明确要求。
