# A 股盘中看盘能力、启动流程与当前架构

状态日期: 2026-06-15

本文档用于收口当前 A 股盘中看盘工具的能力边界、启动/停止方式和整体架构。后续 agent 接手时,优先读本文,再按需读 `AGENTS.md`、`docs/TODO.md` 和 `docs/superpowers/specs/2026-05-13-marketpulse-design.md`。

---

## 1. 盘中能识别和发出的消息

### 1.1 当前监听范围

当前实盘消息第一版只支持 A 股。美股和 Crypto collector 可单独启停,但不参与 A 股盘中消息生成。

当前 A 股样本范围:

- 核心指数: 上证指数、深证成指、创业板指、沪深300、中证500、中证1000、上证50、科创50。
- 采集清单: 约 204 个 A 股标的,来自核心指数标的、15 个固定题材/行业/权重板块、自选股和手动维护的采集标的。
- 题材库: 15 个固定板块,不再扩板块数量;每个题材维护核心股、跟随股、观察股等角色。
- 自选股: 从 watchlist 动态读取,属于实盘消息的重点提醒对象。

关键约束:

- 采集清单不是全 A,宽度指标口径是“当前采集清单宽度”,不是全市场真实宽度。
- A 股 collector 不走代理;日志中应看到 `proxy.skipped`。
- 盘中消息由行情事件、K 线收线事件、CD 信号事件驱动,不是 cron 定时硬扫。

### 1.2 已完善的消息类型

消息最终写入 SQLite 的 `live_messages`,同时写 Redis 最近消息缓存,前端行情面板和 AI 助手读取同一事实源。

#### 大盘指数类

触发源: `bus:quote.tick`

已支持:

- `大盘脉搏偏强`: 8 个核心指数中至少 5 个上涨,平均涨幅达到阈值,且领涨指数涨幅足够。
- `大盘脉搏偏弱`: 8 个核心指数中至少 5 个下跌,平均跌幅达到阈值,且领跌指数跌幅足够。
- `核心指数共振走强`: 上证、沪深300、创业板、中证1000 中至少 3 个涨幅超过阈值。
- `核心指数共振走弱`: 上证、沪深300、创业板、中证1000 中至少 3 个跌幅超过阈值。
- `权重护盘但小票走弱`: 上证50偏强,中证1000明显走弱。
- `小票成长强于权重`: 中证1000/创业板相对上证50明显占优。
- `权重强于小票`: 上证50相对中证1000明显占优。

用途:

- 判断盘面是指数共振、权重护盘、小票扩散,还是指数与个股割裂。
- 给题材状态机提供大盘环境背景。

#### 采集样本宽度类

触发源: `bus:quote.tick`

已支持:

- `采集样本宽度偏强`: 采集清单内非指数标的上涨占比达到阈值,且平均涨跌幅偏强。
- `采集样本宽度偏弱`: 采集清单内非指数标的下跌占比达到阈值,且平均涨跌幅偏弱。
- `采集样本宽度快速恶化`: 以 5 分钟桶起点的采集样本宽度为基准,当前下跌家数和下跌占比快速上升。
- `采集样本宽度快速改善`: 以 5 分钟桶起点的采集样本宽度为基准,当前上涨家数和上涨占比快速上升。
- `采集样本涨停结构增强`: 当前采集清单内近涨停/涨停数量达到比例阈值。
- `采集样本跌停风险扩散`: 当前采集清单内大跌/近跌停数量达到比例阈值。
- `指数偏强但采集样本背离`: 核心指数偏强,但采集清单宽度转弱。

用途:

- 弥补只看指数导致的误判。
- 当前不是全 A 宽度,但对“固定观察池”的盘中强弱变化有实用价值。
- 不依赖东财全 A 快照,不会额外增加盘中 bulk 请求;所有口径均明确标注为“采集清单,非全A”。

#### 题材状态机类

触发源: `bus:quote.tick`

状态机版本: `RULE_VERSION = v3`

已支持:

- `题材启动`: 已采成分中上涨占比达到比例阈值,核心股有响应,领涨股涨幅达标。
- `题材进入扩散`: 上涨占比进一步提高,核心股和跟随股同步扩散。
- `题材进入分歧`: 龙头涨幅大,但上涨占比不足或龙一/龙二断层过大。
- `题材转弱`: 下跌占比达到比例阈值,且拖累股跌幅明显。
- `题材强度回落`: 曾经处于启动/扩散状态后,上涨占比和上涨家数从盘中高点明显回落。
- `核心股切换`: 领涨股从旧核心切换到新核心,且新核心涨幅和相对变化达标。
- `题材走强质量一般`: 上涨占比不低,但核心上涨占比不足。
- `题材异动偏单点`: 龙头涨幅大,但成分扩散不足或断层明显。
- `题材轮动`: 新题材进入启动/扩散,同时旧题材在近 30 分钟内已经转弱/强度回落。
- `题材扩散有成交确认`: 采集清单内多数成分有成交额数据,多只成分涨幅超过 5%,且成交额没有明显集中在单一领涨股。
- `题材成交集中度偏高`: 题材处于启动/扩散,但领涨股成交额占已采成分成交额过高,提示扩散质量待确认。
- `题材涨停结构增强`: 采集清单内出现涨停/近涨停结构代理,用于弥补暂未接入全 A 涨停池的缺口。
- `题材龙头高位回落`: 前一领涨股从高位明显回落,用于代理炸板/退潮风险。

v3 的关键变化:

- 阈值从“固定上涨几只”改为“上涨/下跌/核心响应比例”。
- 小题材和大题材不再用同一套绝对数量判断。
- 状态快照写入 `theme_snapshots`,当前状态写入 `theme_states`,便于盘后复盘。
- `ThemeState.evidence` 追加成交额覆盖、领涨成交占比、近涨停数量、强势股数量等结构字段;这仍是采集清单内代理,不是全市场涨停/炸板统计。

用途:

- 识别主线题材是否从点火走向扩散。
- 识别“只有龙头、没有板块”的单点行情。
- 识别题材内部核心股切换和强度退潮。

#### 自选股类

触发源: `bus:quote.tick`

已支持:

- `自选股翻红`: 自选股涨跌幅从负转正。
- `自选股翻绿`: 自选股涨跌幅从正转负。
- `自选股波动扩大`: 自选股首次越过 `2%` 或 `5%` 绝对涨跌幅阈值。
- `自选股逆题材走弱`: 题材内多只成分上涨,但该自选股跌幅超过阈值。

用途:

- 让自选股不只是列表,而是能在盘中提示状态变化。
- 对“题材强但持有/关注标的不跟”的情况给出风险提示。

#### CD 信号类

触发源: `bus:signal.new`

已支持:

- `某标的触发 15m/30m/60m/4h/1d CD 买入信号`
- `某标的触发 15m/30m/60m/4h/1d CD 卖出信号`

当前路径:

- 5m 收线或派生周期聚合后发布 `bus:bars.updated`。
- `signal_scan_consumer` 只读已入库 K 线扫描 CD。
- 新信号发布 `bus:signal.new`。
- `live_message_consumer` 转换为实盘消息。

用途:

- 提供规则化技术信号,不做交易执行。
- 可以作为 AI 助手上下文的一类事实。

#### 5m 放量类

触发源: `bus:bars.updated`

已支持:

- 自选股或题材成分 `5m明显放量`。
- 当前 5m 成交量达到最近样本均量的 `2.5` 倍以上。
- 如果该 5m K 线收跌,消息级别为风险;上涨或持平为观察。

用途:

- 提示自选股/题材成分在 5m 周期出现明显成交量突变。

### 1.3 现在前端如何展示

行情面板:

- `LiveMessagesPanel` 调 `/api/live-messages`。
- 显示消息级别、分类、标题、正文、证据字段和相关标的。
- 状态条显示监听题材数、成分股数、自选股数和最新消息。

AI 助手:

- 当前不是 LLM 自动推理,而是基于 `/api/live-messages/ai-context` 拉最近 30 分钟实盘消息。
- 页面按“题材主线 / 自选异动 / CD 信号 / 风险点”分组汇总。
- 这是规则事实源的摘要层,不是独立的数据采集层。

### 1.4 仍待完善的消息能力

短期优先级较高:

- 全 A 宽度: 已有采集清单低成本代理,但仍无法代表全市场真实上涨/下跌家数、涨停/跌停家数、炸板率。
- 涨停梯队/连板结构: 已有采集清单内近涨停/龙头回落代理,但没有稳定涨停状态源,无法识别一进二、二进三、空间板、断板潮。
- 资金流驱动的题材确认: 已有部分资金流基础,实盘消息状态机已纳入成交额结构代理,但尚未把主力净流入、板块资金流和个股资金流纳入核心规则。
- 题材内部强弱排名连续性: 当前有快照和状态,但还没有“连续 N 个 5m 上升/下降”的趋势确认。
- 消息严重程度校准: `watch/warning/critical` 目前以规则粗分,后续需要按实盘复盘调整阈值。

中期能力:

- 指数分时结构: 当前主要用快照涨跌幅,还不能识别指数 5m 级别突破/跌破、V 型反转、午后回落。
- 个股盘口/委买委卖: 免费数据源下当前没有盘口深度,不能识别封单强弱、撤单、扫板。
- 新闻/公告/互动易: 当前不接新闻事件,无法解释突发异动原因。
- AI 推理层: 当前 AI 助手是规则消息摘要,还没有引入 LLM 做“多消息归因、优先级排序、操作风险提示”。
- 全市场行情成本控制: 如果扩到 300+ 或更大,需要进一步分层调度、优先级队列和失败退避。

---

## 2. 启动、停止与日常运维流程

### 2.1 推荐启动方式

后台启动全部服务:

```bash
make dev-bg
```

查看状态:

```bash
make dev-status
```

停止全部后台服务和 Redis:

```bash
make dev-stop-bg
```

打开前端:

```text
http://localhost:3000/dashboard
```

### 2.2 单独启停某个进程

服务名:

- `collector-ashare`
- `collector-us`
- `collector-crypto`
- `api`
- `web`

只启动 A 股采集:

```bash
make dev-bg ARGS="collector-ashare"
```

只停止美股和加密采集:

```bash
make dev-stop-bg ARGS="collector-us collector-crypto"
```

只看 A 股采集状态:

```bash
make dev-status ARGS="collector-ashare"
```

直接脚本方式:

```bash
bash scripts/dev-start.sh collector-ashare
bash scripts/dev-stop.sh collector-us
bash scripts/dev-stop.sh collector-crypto
bash scripts/dev-status.sh collector-ashare
```

注意:

- `make dev-stop-bg` 不带 `ARGS` 会全停并停止 Redis。
- `make dev-stop-bg ARGS="collector-us"` 只停指定进程,不动 Redis。
- A 股盘中看盘只需要 Redis、`collector-ashare`、API、Web。

### 2.3 当前建议的盘中启动组合

A 股盘中看盘:

```bash
make dev-bg ARGS="collector-ashare api web"
make dev-status
```

如果美股和 Crypto 暂时不用:

```bash
make dev-stop-bg ARGS="collector-us collector-crypto"
```

当前实测状态应类似:

```text
ok   redis
ok   collector-ashare
down collector-us
down collector-crypto
ok   api
ok   web
```

### 2.4 日志位置

短期滚动日志:

```bash
tail -f /tmp/marketpulse/collector-ashare.log
tail -f /tmp/marketpulse/api.log
tail -f /tmp/marketpulse/web.log
```

结构化长期日志:

```bash
tail -f data/logs/collector_ashare.log
tail -f data/logs/collector_ashare-errors.log
tail -f data/logs/api.log
tail -f data/logs/api-errors.log
tail -f data/logs/fault.log
```

常用排查:

```bash
grep "proxy.skipped" data/logs/collector_ashare.log | tail
grep "live_message.generated" data/logs/collector_ashare.log | tail
grep "reconcile.daily_failed\|reconcile.daily_retry_failed\|breaker_open" data/logs/collector_ashare-errors.log | tail
grep "FATAL" /tmp/marketpulse/collector-ashare.log data/logs/fault.log
```

### 2.5 A 股采集启动后的关键阶段

`collector-ashare` 启动后:

1. 初始化日志和代理策略: A 股应直连,不走代理。
2. 初始化 `bars_ashare.duckdb`、`intraday_ashare.duckdb`、SQLite state。
3. 初始化 Redis、ak middleware、ratelimit、breaker、ak worker pool。
4. 导入默认 watchlist、symbol directory seed、题材 seed、采集清单 seed。
5. 启动 Redis consumer:
   - refill consumer
   - signal scan consumer
   - live message consumer
   - collector symbol consumer
6. 启动 A 股 K 线和行情任务:
   - `bar_poller`: 采集清单驱动 5m 收线采集。
   - `quote_bar_ticker`: quote 驱动进行中 1d/5m 态。
   - `intraday_line_writer`: 分时线入库。
   - `daily_settlement`: 收盘后完成度驱动日线补齐。
7. 启动 reconcile:
   - 如果处于 A 股开盘时段,跳过深历史 reconcile,保护盘中采集。
   - 非开盘时段,按采集清单顺序节流补齐 1d/5m 和派生周期。

### 2.6 收盘后日线补齐策略

当前不使用 cron 硬触发日线补齐,而是:

- `daily_settlement` 检测 A 股 session 从 open 到 closed 的边沿。
- 收盘后读取采集清单。
- 先查 DuckDB 中已有当日 closed 1d 的标的,已完成则跳过。
- 对 pending 标的逐个拉 1d,默认每标的 `1.5s` 节流,并加入随机抖动。
- 当日 1d 补齐后聚合 1wk/1mo。
- 超过 deadline 仍未补齐的标的记录 warning,留给次日或启动 reconcile 兜底。

这个策略的目标:

- 避免 204 个标的在收盘后一瞬间集中请求。
- 避免已收线标的重复请求。
- 保留 SQLite/DuckDB 复盘事实,不依赖 Redis 消息长期存在。

---

## 3. 当前整体架构

### 3.1 进程边界

```text
Next.js Web(3000)
        |
        v
FastAPI API(8787)  -- 只读 Redis / SQLite / collector HTTP
        |
        +---------------- Redis ----------------+
        |                                       |
collector-ashare(8788)                  collector-us / collector-crypto
        |
        +-- A 股外部数据源: sina 为主,EM/其他源按 adapter 兜底
        +-- DuckDB: data/bars_ashare.duckdb, data/intraday_ashare.duckdb
        +-- SQLite: data/state.db
```

当前盘中重点是 `collector-ashare`。美股和 Crypto 可停,不影响 A 股实盘消息。

### 3.2 数据存储边界

DuckDB:

- `data/bars_ashare.duckdb`: A 股 K 线,包括 5m、派生周期、1d、1wk、1mo。
- `data/intraday_ashare.duckdb`: A 股分时线。

SQLite:

- `collector_symbols`: 当前采集清单,手动和 seed 都落这里。
- `theme_universe`: 题材定义。
- `theme_constituents`: 题材成分股。
- `theme_snapshots`: 题材盘中快照。
- `theme_states`: 题材当前状态。
- `live_messages`: 实盘消息事实源。
- watchlist、signals、directory 等关系数据。

Redis:

- 作为进程间事件总线和最新缓存。
- 关键 stream:
  - `bus:quote.tick`
  - `bus:bars.updated`
  - `bus:signal.new`
  - `bus:live.message`
  - `bus:bars.refill_request`
- Redis 消息不是最终复盘事实源;关键消息和状态必须写 SQLite 或 DuckDB。

### 3.3 A 股盘中数据流

```text
collector_symbols
    |
    v
collector-ashare
    |
    +-- quote tick ---------------> bus:quote.tick
    |                                  |
    |                                  v
    |                          LiveMessageService
    |                                  |
    |                                  +-- live_messages(SQLite)
    |                                  +-- bus:live.message / Redis recent cache
    |
    +-- 5m 收线 -------------------> bars_ashare.duckdb
    |                                  |
    |                                  +-- aggregate 15m/30m/60m/4h
    |                                  +-- bus:bars.updated
    |                                          |
    |                                          +-- signal_scan_consumer -> bus:signal.new
    |                                          +-- LiveMessageService -> 5m 放量消息
    |
    +-- quote 驱动进行中 1d/5m -----> bars_ashare.duckdb(final=false)
    |
    +-- 收盘 daily_settlement ------> 1d(final=true) + 1wk/1mo
```

前端读取:

```text
行情面板 LiveMessagesPanel -> /api/live-messages
AI 助手                 -> /api/live-messages/ai-context
设置/采集标的           -> /api/collector-symbols
题材库                 -> /api/themes
K 线                   -> /api/symbols/{symbol}/bars 或 collector history route
```

### 3.4 代码入口

实盘消息:

- `core/services/live_message_service.py`
- `apps/collector/jobs/live_message_consumer.py`
- `core/persistence/live_message_repo.py`
- `apps/api/routes/live_messages.py`
- `apps/web/components/LiveMessagesPanel.tsx`
- `apps/web/app/assistant/page.tsx`

A 股采集:

- `apps/collector/ashare/main.py`
- `apps/collector/ashare/bar_poller.py`
- `apps/collector/ashare/quote_bar_ticker.py`
- `apps/collector/ashare/daily_settlement.py`
- `apps/collector/startup_reconcile.py`

题材和采集清单:

- `core/themes/seeds/ashare_themes.json`
- `core/themes/seeds/ashare_themes_expansion.json`
- `core/themes/seed_loader.py`
- `core/collector_symbols/seed_loader.py`
- `apps/api/routes/themes.py`
- `apps/api/routes/collector_symbols.py`
- `apps/web/app/settings/themes/page.tsx`

运维脚本:

- `scripts/dev-start.sh`
- `scripts/dev-stop.sh`
- `scripts/dev-status.sh`
- `Makefile`

### 3.5 架构原则

- 采集任务只从 `collector_symbols` 获取标的,不直接依赖题材库或自选股。
- 题材库和自选股可以影响 seed 或用户维护,但最终都应体现到采集清单。
- API 进程不直接访问 A 股外部接口;外部数据访问在 collector/adapter 内。
- A 股 akshare 调用必须走 `core/integrations/akshare.py::ak_call`。
- A 股 collector 直连,不走代理。
- Redis 是实时事件总线,SQLite/DuckDB 是可复盘事实源。
- 单标的失败只记 warning 并继续,不能拖死整批采集。

---

## 4. 今日完成状态

截至 2026-06-15:

- A 股采集清单扩到约 204 个标的。
- 15 个题材保持不变,题材成分扩充到 197 条。
- 题材状态机升级到比例阈值 `v3`。
- 已补题材轮动消息: 新题材启动/扩散 + 旧题材近 30 分钟转弱/回落。
- 已补题材成交结构和涨停结构代理: 成交确认、成交集中、涨停结构增强、龙头高位回落。
- 已补采集样本宽度低成本代理: 宽度快速恶化/改善、样本涨停结构、样本跌停风险。
- 收盘后日线补齐改为跳过已收线 + 节流 + 抖动。
- 美股和 Crypto collector 已支持单独启停,并已停止。
- A 股 collector、API、Web、Redis 保持运行。
- 文档已补齐本文、README 启停说明、TODO 后续项。

---

## 5. 下一步建议

优先级最高:

1. 接入真正的全 A 宽度稳定数据源,替换“采集清单宽度”的局限。
2. 在题材状态机中接入真正的主力净流入、板块资金流和核心股资金流,减少纯涨跌幅噪声。
3. 增加真实涨停池、连板高度和炸板率识别,这是 A 股短线盘中判断的重要缺口。
4. 开盘实盘复测阈值,记录误报/漏报,再调 `RULE_VERSION`。
