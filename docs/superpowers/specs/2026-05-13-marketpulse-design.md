# MarketPulse 设计文档

- **日期**:2026-05-13
- **版本**:v1 draft
- **状态**:草案(待审阅)

## 0. 目标与范围

MarketPulse 是一个本地运行的 Web 行情监控分析平台,覆盖 A 股、港股、美股、Web3(Crypto)四个市场,为个人投研决策提供:

1. **大盘分析**:四市场统一概览(指数、成分涨跌、热力图、资金面)
2. **重要事件提醒 + 影响面分析**:实时抓取事件源,LLM 判定影响范围/方向/置信度,推送到前端
3. **安全买入信号**:多因子加权 + 硬过滤一票否决,每市场产出 Top-10 候选

参考 [stockanalysis.com](https://stockanalysis.com) 的信息架构,但重点放在"事件驱动 + 多市场横向对比",而不是做它的中文翻版。

### 设计原则

- 开源优先:第三方库用 MIT/Apache 授权的成熟项目
- 免费优先:所有主源都使用免费层(akshare/yfinance/Binance WS 等)
- 时效性:Crypto 亚秒级,A/HK/US 10s 级,事件 30s–1min
- 单机可跑:一条 `make dev` 起来,缺任何源都能优雅降级

### V1 明确不做的事

- ❌ 个股详情页(v2)
- ❌ 预测复盘(v2)
- ❌ 交易执行 / 模拟持仓
- ❌ 用户 / 权限系统
- ❌ 回测框架
- ❌ 高可用 / 多实例 / 告警通知

---

## 1. 系统总览与边界

### 1.1 定位

决策支持工具,不做执行。输入是"四市场的行情 + 事件",输出是"一眼能看懂的大盘态势、有理由的买入候选、有影响面分析的事件流"。

### 1.2 四市场 V1 数据覆盖

| 市场 | 主源 | 备源 | 事件源 | 刷新粒度 |
|---|---|---|---|---|
| A 股 | akshare(东财/新浪) | mootdx(TDX) | 财联社电报 / 同花顺 7x24 | 盘中 10s 轮询 |
| 港股 | akshare(东财港股) | yfinance `.HK` | 港交所披露易 | 同上 |
| 美股 | Alpaca IEX(免费实时)| yfinance(curl_cffi) | SEC EDGAR 8-K / GDELT | 同上 |
| Crypto | Binance WS + OKX WS | CoinGecko REST | CryptoPanic / Binance Announcements | 亚秒级推送 |

### 1.3 V1 验收标准

- **V1-A1**:Dashboard 一屏展示 4 市场大盘指数、TOP 涨跌、行业/板块热力图
- **V1-A2**:事件流实时推送,每条带 LLM 影响面卡片(范围/方向/置信度/理由)
- **V1-A3**:每市场产出每日 Top-10 安全买入候选,点开看得到因子拆解和推荐理由链
- **V1-A4**:`make dev` 一键启动,任意数据源故障都能优雅降级,UI 诚实标注降级状态

---

## 2. 组件划分

7 个有独立职责的模块,每个可单独测试/替换。

### 2.1 Market Adapters(行情适配层)

每市场一个独立适配器,统一产出 `Bar / Quote / Fundamental` 三种领域模型。

| 适配器 | 主源 | 备源 | 备注 |
|---|---|---|---|
| `AShareAdapter` | akshare(东财/新浪) | mootdx(通达信 TDX) | 日内快照 10s 轮询 |
| `HKAdapter` | akshare(东财港股) | yfinance `.HK` | 同上 |
| `USAdapter` | Alpaca IEX(免费实时) | yfinance(curl_cffi) | 盘后 EDGAR 基本面 |
| `CryptoAdapter` | Binance WS + OKX WS | CoinGecko REST | 真正的实时推送 |

统一接口:

```python
class MarketAdapter(Protocol):
    market: str
    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]: ...
    async def subscribe(self, symbols: list[str], on_bar: Callable) -> None: ...
    async def fetch_history(self, symbol: str, start, end) -> list[Bar]: ...
    async def health(self) -> HealthStatus: ...
```

切源只改这一层;业务层不感知数据来源。

### 2.2 Factor Engine(因子引擎)

纯计算,无 I/O。输入 Bar 序列,输出 `BuyCandidate{symbol, score, sub_scores, reason_chain}`。四子因子:

- `value_factor`:估值(PE/PB 分位、EV/EBITDA;Crypto 用 MVRV/NVT)
- `momentum_factor`:动量(RS、MA 排列、20d 收益率)
- `event_factor`:事件驱动(近 7d 事件 direction × confidence × decay 加权)
- `risk_factor`:风险惩罚(波动率、回撤、流动性)

加权公式走 YAML 配置,不改代码可调参。

### 2.3 Event Pipeline(事件管道)

三级串行:`Collectors → Rule Pre-filter → LLM Impact Analyzer → Event Store`

- Collectors:每源独立类,熔断隔离
- Rule Pre-filter:关键词 + 实体过滤,去掉 ~80% 噪音
- LLM Impact Analyzer:产出结构化 `{scope, direction, confidence, affected_symbols, reasoning, time_horizon}`
- Event Store:无论是否进 LLM 都落库

### 2.4 Scheduler(调度器)

APScheduler,四种 tick:

- **10s**:抓实时快照 → 推前端 WS
- **1min**:扫事件新源、算滚动短因子
- **15min**:跑完整因子引擎 → 刷新 Top-10
- **T+0 收盘后**:全量重算历史因子、写 DuckDB

### 2.5 Persistence Layer

- **DuckDB**(`bars.duckdb`):分区表 `bars_{market}`,列存 + 压缩,历史 K 线/因子快照
- **SQLite**(`events.db`):事件流、信号快照、用户关注列表,小表高频写
- 均走仓储模式(`BarRepo` / `EventRepo`),业务层不直接碰 SQL

### 2.6 API Layer(FastAPI)

- REST:`/api/markets/{market}/overview`、`/api/candidates`、`/api/events`、`/api/symbols/{sym}/bars`
- WebSocket:`/ws/ticks`、`/ws/events`、`/ws/candidates`
- 路由无业务逻辑,只做"校验 → 调 service → 序列化"

### 2.7 Frontend(Next.js App Router + SPA)

4 个页面对齐 V1 验收标准:

- `/dashboard` —— 四市场大盘卡片 + 热力图
- `/events` —— 事件流(带影响面标签、过滤器)
- `/candidates` —— 每市场 Top-10 候选 + 因子拆解弹窗
- `/settings` —— 阈值/权重/关注列表可视化配置

图表统一用 TradingView Lightweight Charts(Apache-2.0)。

### 2.8 依赖方向

```
Frontend -> API Layer -> Factor Engine / Event Pipeline -> Market Adapters -> Persistence
                                     \___________________\_______________/
                                        Scheduler 从侧面驱动
```

LLM 换模型不影响因子引擎;换数据源只改 adapter;前端可独立 mock API 调试。

---

## 3. 数据流

三条主要数据路径,按"推送实时性"从强到弱排列。

### 3.1 路径 A:实时行情 tick → 前端(硬实时 <1s)

```
Crypto: Binance/OKX WS ────┐
A/HK:   akshare 10s 轮询 ──┤
US:     Alpaca IEX WS  ────┤
                           ▼
                  [Market Adapter] 统一成 Quote
                           │
                 ┌─────────┴──────────┐
                 ▼                    ▼
         [In-Memory Cache]     [DuckDB 批量写]
         (dict + TTL)          (每 60s flush,避免小写放大)
                 │
                 ▼
         [WS Broadcaster]
                 │
                 ▼
         Frontend /ws/ticks
         (前端订阅自己可见的 symbols,不全推)
```

关键:内存是实时源头,DB 是异步归档。前端从不直接查 DB 拿最新价。

### 3.2 路径 B:事件 → LLM 影响面 → 前端(准实时 10s–1min)

```
[Collectors 并行 pull]
  GDELT (15min)          ──┐
  财联社电报 RSS (30s)    ──┤
  CryptoPanic API (1min) ──┤
  SEC 8-K RSS (5min)     ──┤
  港交所披露易 (5min)     ──┤
                           ▼
                  [Rule Pre-filter]
                  关键词表 + 实体映射(NER-lite)
                  ├─ 命中 → 进 LLM 队列
                  └─ 未命中 → 丢或入"次要事件"桶
                           │
                           ▼
                  [LLM Impact Analyzer]
                  输入:事件文本 + 近期价格上下文
                  输出:{scope, symbols[], direction,
                        confidence, reasoning}
                  并发上限 = 3,失败走规则降级
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
          [events.db 落库]    [WS /ws/events 推送]
                 │
                 ▼
          触发因子引擎 event_factor 增量更新
          (只对 affected_symbols 重算)
```

降级链:LLM 失败 → 规则标签(低置信度);LLM 限流 → 排队不丢事件;所有事件无论是否进 LLM 都落库。

### 3.3 路径 C:因子引擎 → 买入候选(15min 滚动 + 日终全量)

```
[Scheduler 15min tick]
         │
         ▼
  [拉取每市场 universe]
  (A:HS300 + CSI500,HK:HSCI,
   US:S&P500 + NASDAQ100,Crypto:市值前 200)
         │
         ▼
  [从 DuckDB 拉最近 120 日 bars]
         │
         ▼
  [Factor Engine 并行计算]
   value / momentum / event / risk 四路并发
         │
         ▼
  [加权合成 → 每市场排序取 Top-10]
         │
         ▼
  [写 signals 表,带 snapshot_ts]
  (保留历史,方便 v2 做预测复盘)
         │
         ▼
  [WS /ws/candidates 广播刷新事件]
  前端拉 /api/candidates 取最新快照
```

为什么 15min 而不是实时:因子计算需要完整 bar,估值数据日更,LLM 事件有延迟 —— 15min 刚好让所有输入稳定,且对"安全买入"决策足够新。

### 3.4 内存缓存与 DB 的边界

| 数据 | 存哪里 | 为什么 |
|---|---|---|
| 最新 quote(<60s) | 内存 dict | 高频读写,DB 扛不住 |
| 近 24h bars | 内存 LRU + DuckDB | 前端 K 线图主要命中内存 |
| 历史 bars | DuckDB | 列存压缩,查询快 |
| 事件、信号、配置 | SQLite | 小表、关系查询、事务 |
| LLM 分析缓存 | SQLite(按 content_hash) | 同一事件不重复调 LLM |

### 3.5 冷启动流程

```
make dev
  │
  ├─ 1. 校验 .env、API keys、DB 文件 → 缺啥报啥(不崩)
  ├─ 2. 启动 FastAPI(API 层可响应,candidates 返回"预热中")
  ├─ 3. 异步拉历史 bars 填充 DuckDB(首次 5-10 分钟)
  ├─ 4. 启 WS 连接(Binance 先,国内源错峰)
  ├─ 5. 第一轮因子引擎跑完 → 前端切正常态
  └─ 6. Scheduler 进入稳态
```

任何一步失败都不阻塞后续 —— 例如 Alpaca key 没配,就禁用美股 tab 并在 UI 上标灰,其他市场照常。

---

## 4. 多因子买入信号

### 4.1 设计目标

"安全买入" ≠ "必涨的票"。V1 定义为:**估值不贵 + 趋势向上 + 近期无负面事件 + 风险可控**的标的。宁可错过,不要踩雷。

### 4.2 四因子体系

每个子因子都输出 `[0, 100]` 归一化分数,便于跨市场比较。

**① Value Factor(估值,默认权重 0.30)**

| 市场 | 计算方式 |
|---|---|
| A/HK/US | `PE_TTM`、`PB`、`EV/EBITDA` 在行业内的分位数,取反(分位越低分越高) |
| Crypto | `MVRV_Z_Score`、`NVT_Signal`、`Realized_Cap/Market_Cap`,分位反转 |

三个子指标等权合成。数据缺失则该指标跳过(不补 0),剩余归权。

**② Momentum Factor(动量,权重 0.25)**

- `RS_20d`:相对所属基准(HS300 / HSI / SPX / BTC)的 20 日超额收益
- `MA_alignment`:5/20/60 日均线排列打分(完美多头 = 100,死叉 = 0)
- `Vol_trend`:近 5 日成交量 / 过去 20 日均量,放量上涨加分

三个子指标等权。

**③ Event Factor(事件驱动,权重 0.25)**

从事件管道拉该 symbol 过去 7 日的事件:

```
event_score = Σ (direction_i × confidence_i × decay(age_i))
  其中 direction ∈ {-1, 0, +1}
       confidence ∈ [0, 1]
       decay(age) = exp(-age_days / 3)
```

再 min-max 归一到 `[0, 100]`。无事件 → 50(中性)。LLM 降级期使用规则打分,置信度上限 0.5。

**④ Risk Factor(风险惩罚,权重 0.20)**

- `volatility_20d`:年化波动率分位(高分位扣分)
- `max_drawdown_60d`:近 60 日最大回撤(越深扣越多)
- `liquidity`:A/HK/US 用日均成交额,Crypto 用 24h volume / market cap

输出归一化为 `[0, 100]`,这里 100 = 风险低(已经反转)。

### 4.3 合成公式

```python
score = (
    w_value    * value_factor    +
    w_momentum * momentum_factor +
    w_event    * event_factor    +
    w_risk     * risk_factor
)
# 默认 w = (0.30, 0.25, 0.25, 0.20),sum = 1.0
```

### 4.4 硬过滤器(先筛再打分)

| 规则 | A/HK/US | Crypto |
|---|---|---|
| 流动性下限 | 日均成交额 < 2000 万 剔除 | 24h volume < $5M 剔除 |
| ST / 退市风险 | 剔除 ST、*ST、退市整理期 | 剔除已下架、稳定币 |
| 新股冷静期 | 上市 < 60 日剔除 | 上币 < 30 日剔除 |
| 停牌 | 停牌中剔除 | 交易暂停中剔除 |
| 负面事件一票否决 | LLM `direction=-1 且 confidence>0.7` 的事件 7 日内 → 剔除 | 同 |

一票否决是"安全"的底线 —— 宁可漏掉反弹机会,不把可能暴雷的摆到用户眼前。

### 4.5 配置示例 `config/factors.yaml`

```yaml
weights:
  value: 0.30
  momentum: 0.25
  event: 0.25
  risk: 0.20

universe:
  ashare: ["hs300", "zz500"]
  hk: ["hsci"]
  us: ["sp500", "nasdaq100"]
  crypto: { top_n_by_mcap: 200 }

filters:
  min_liquidity_cny: 20_000_000
  min_crypto_volume_usd: 5_000_000
  exclude_new_days: 60
  negative_event_veto:
    direction: -1
    min_confidence: 0.7
    lookback_days: 7

per_market_overrides:
  crypto:
    weights: { value: 0.20, momentum: 0.35, event: 0.25, risk: 0.20 }
```

每市场可 override 默认值 —— Crypto 估值因子降权、动量加权,因为链上估值指标噪声大。

### 4.6 可解释性:`reason_chain`

```json
{
  "symbol": "000858.SZ",
  "score": 78.5,
  "sub_scores": {
    "value": 82, "momentum": 75, "event": 70, "risk": 85
  },
  "top_reasons": [
    "PE_TTM 处于行业 15% 分位(便宜)",
    "5/20/60 日均线多头排列",
    "过去 7 日无重大负面事件",
    "20 日波动率处于历史 30% 分位(低波)"
  ],
  "warnings": ["近 5 日成交量略萎缩"]
}
```

前端弹窗就是这个结构,用户能判断"这条推荐我认不认"。

### 4.7 V1 不做的事

- ❌ ML 选股(XGBoost / LSTM 等),v2 再上
- ❌ 回测框架(v2 跟预测复盘一起建)
- ❌ 自动交易 / 模拟盘持仓
- ❌ 期权、可转债、衍生品因子

---

## 5. 事件 + LLM 影响面管道

### 5.1 事件源清单

| 源 | 市场 | 拉取方式 | 频率 | 免费 |
|---|---|---|---|---|
| 财联社电报 | A/HK | RSS / 社区 WS | 30s | ✓ |
| 同花顺 7x24 | A/HK | HTTP 轮询 | 1min | ✓ |
| 港交所披露易 | HK | RSS + HTML 解析 | 5min | ✓ |
| SEC EDGAR 8-K | US | Atom feed | 5min | ✓ |
| GDELT 2.1 | 全球宏观 | 15min doc API | 15min | ✓ |
| CryptoPanic | Crypto | REST API(free 500/day) | 1min | ✓ |
| Binance Announcements | Crypto | HTML 轮询 | 2min | ✓ |

每源一个 `Collector` 类,统一输出 `RawEvent{source, ts, title, body, url, mentioned_entities[]}`。

### 5.2 Rule Pre-filter

```
RawEvent 进入 → [去重 content_hash 7 日内]
              → [实体识别 jieba + 8k 词典]
                 输出 mentioned_symbols
              → [关键词打分]
                高(+3):重组/违约/立案/暴雷/停牌/减持/sec charges/hack/rug
                中(+1):业绩/财报/签约/增持/upgrade/partnership
              → if mentioned_symbols 非空 or 关键词分 ≥ 3:
                   进 LLM 队列
                else:
                   入"次要事件"桶(只入库)
```

过滤后实际进 LLM 的事件约为原流量的 15-25%。

### 5.3 LLM Impact Analyzer

**模型选型**:本地 `Qwen2.5-7B-Instruct`(Ollama)默认,配 OpenAI 兼容接口可切云端(DeepSeek-V3 / GPT-4o-mini)。配置项切换,不改代码。

**Prompt 结构(System + User 分离)**:

```
SYSTEM:
你是金融事件影响分析师。只返回 JSON,不要任何解释。
JSON schema:
{
  "scope": "micro|meso|macro",     // 个股/行业/市场
  "direction": -1 | 0 | 1,          // 负面/中性/正面
  "confidence": 0.0-1.0,
  "affected_symbols": ["..."],      // 代码,最多 5 个
  "affected_sectors": ["..."],      // 行业名,最多 3 个
  "reasoning": "一句话,不超过 50 字",
  "time_horizon": "intraday|short|medium"
}

USER:
【事件】{title}
【正文】{body[:500]}
【发生时间】{ts}
【已识别提及】{mentioned_symbols}
【近期价格上下文】{symbol}:近 5 日 {change_pct}%,今日成交量 {vol_ratio}x 均量
```

Context 里放价格信息很关键 —— 让模型在"财报超预期"和"财报超预期但股价已涨 30%"之间区分判断。

### 5.4 并发、限流、降级

| 层级 | 策略 |
|---|---|
| 队列 | 内存 `asyncio.Queue`,上限 500,溢出老事件丢弃并告警 |
| 并发 | semaphore = 3(本地)或 = 8(云端) |
| 超时 | 单次 15s |
| 重试 | 1 次,指数退避 2s |
| 熔断 | 5 分钟内失败率 > 50% → 暂停 10 分钟,期间走规则降级 |
| 缓存 | (content_hash → analysis)7 日 TTL |

**规则降级**(LLM 不可用时):

```python
def fallback_analyze(event):
    direction = (-1 if neg else +1 if pos else 0)
    confidence = min(0.5, keyword_score / 10)  # 上限 0.5,不触发硬过滤
    scope = "micro" if mentioned_symbols else "meso"
    return Analysis(direction, confidence, mentioned_symbols[:5],
                    "[规则降级] 关键词匹配", ...)
```

降级产出在 UI 上加 `降级` 标签。

### 5.5 成本控制

- 本地模型默认:零 token 成本,单次 ~2s(M 系列 Mac),够用
- 云端模型:`max_daily_tokens` 上限,达到后自动切规则降级
- 流量估算:四市场每日 RawEvent ~1500 条,过滤后 ~300 条进 LLM,每条 ~800 tokens,总计 ~24 万 tokens/日,DeepSeek-V3 约 ¥0.3/日

### 5.6 事件表结构

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_event_id TEXT,
  market TEXT,                      -- ashare/hk/us/crypto/global
  ts TIMESTAMP NOT NULL,
  title TEXT NOT NULL,
  body TEXT,
  url TEXT,
  content_hash TEXT UNIQUE,
  scope TEXT,
  direction INTEGER,
  confidence REAL,
  affected_symbols TEXT,            -- JSON array
  affected_sectors TEXT,
  reasoning TEXT,
  time_horizon TEXT,
  analyzer TEXT,                    -- llm:qwen2.5 / rule:fallback
  analyzed_at TIMESTAMP,
  INDEX idx_ts (ts DESC),
  INDEX idx_market_ts (market, ts DESC)
);
```

`affected_symbols` 用 JSON 存,避免多对多表。

### 5.7 前端事件流 UX

- 时间轴倒序,无限滚动
- 卡片:标题 + 影响面标签(`个股/行业/市场`)+ 方向徽章(红/灰/绿)+ 置信度条 + LLM 理由
- 顶部筛选:市场、方向、置信度阈值、时间段
- 点开展开:原文、affected_symbols(可跳 K 线图)、完整 reasoning
- 触发某 symbol 一票否决的事件,在 candidates 页显示"因 [事件标题] 临时剔除"

---

## 6. 错误处理、优雅降级、测试

### 6.1 故障矩阵

| 组件 | 故障场景 | 降级行为 | UI 表现 |
|---|---|---|---|
| AShareAdapter(akshare) | 网络异常/接口改版 | 切 mootdx 备源,失败 3 次熔断 5min | A 股卡片显示"数据源切备,可能延迟" |
| USAdapter(Alpaca) | key 失效/未配置 | 切 yfinance,延迟 15min | 标"延迟行情",Top-10 照常出 |
| CryptoAdapter(Binance WS) | 连接断开 | 指数退避重连(1s→2s→…→30s),期间走 REST 轮询 | 数据延迟徽章,不影响使用 |
| 事件 Collector | 单源 404/超时 | 该源独立熔断,其他源照常 | 事件流顶部小黄条"X 源不可用" |
| LLM Analyzer | 本地模型崩 / 云端 429 | 切规则降级 | 事件卡片标 `降级` 徽章 |
| Factor Engine | 单 symbol 计算异常 | 跳过该 symbol,记日志 | candidates 列表少一条 |
| DuckDB | 文件锁/磁盘满 | 停止归档,内存继续服务,读接口降级 | 历史 K 线标"回溯数据不可用" |
| SQLite | 同上 | 事件流进内存 buffer(上限 1000) | 顶部红条"事件未持久化" |
| Scheduler | 某 job 卡死 | APScheduler job timeout 5min 强杀 | 对用户透明 |

核心:任何单点故障都不能让整个服务 502。Adapter 挂了只影响对应市场 tab,LLM 挂了事件流照常,只是少了影响面判断。

### 6.2 启动时校验(Fail-Informative,不 Fail-Fast)

```
make dev
  │
  ├─ 读 .env / config/*.yaml,缺字段列清单(不退出)
  ├─ 测试每个 adapter 的 health check
  │    ✓ akshare: 拉沪深指数最新报价
  │    ✗ Alpaca: key 未配 → 标记 US adapter disabled
  │    ✓ Binance WS: ping
  │    ✗ CryptoPanic: 429 → 标记 source disabled
  ├─ 测试 Ollama 是否在跑 → 否则切云端或规则降级
  ├─ 生成启动健康报告 → 终端 + /api/health
  └─ FastAPI 起来,前端 /dashboard 看到哪些市场 tab 灰色
```

缺 API key 不应该让整个应用起不来 —— 用户可能就想先看 A 股和 Crypto。**能跑多少先跑多少,UI 诚实地告诉用户缺什么。**

### 6.3 日志与可观测性

- 结构化日志:`structlog` + JSON 输出,字段统一为 `ts/level/component/market/symbol/event`
- 分级:
  - `INFO`:正常业务
  - `WARN`:可恢复异常(源切换、单次重试)
  - `ERROR`:熔断、LLM 连续失败、DB 写失败
- 日志文件:`logs/app.log` 按日滚动,保留 7 天
- `/api/health` 返回:

```json
{
  "status": "degraded",
  "adapters": {
    "ashare": "ok", "hk": "ok",
    "us": "disabled: missing ALPACA_KEY",
    "crypto": "ok"
  },
  "llm": "ok: qwen2.5-7b",
  "last_candidates_refresh": "2026-05-13T10:15:00+08:00",
  "event_queue_depth": 12
}
```

- `/debug` 简易监控页(仅本地访问):/api/health 可视化 + 最近 100 条 WARN/ERROR

V1 不引入 Prometheus/Grafana,日志 + health 端点足够。

### 6.4 测试策略

**① 单元测试(pytest,目标 ~150 个)**

- Factor Engine 每个子因子:历史 bar → 稳定分数(快照测试)
- Rule Pre-filter:关键词打分、实体识别边界
- LLM Analyzer 的 prompt 构造、JSON 解析、降级路径(模型 mock)
- 合成公式、硬过滤规则
- 所有纯函数

**② 集成测试(~40 个)**

- Adapter 层:**不 mock,跑真 HTTP**(`@pytest.mark.integration`,CI 不跑,本地手动)。原因:akshare/yfinance 这类库接口经常改版,mock 会让我们假装没问题
- Event Pipeline:造一批 RawEvent,跑完整 pipeline,断言 DB 终态
- Scheduler:`FakeScheduler` 跑 tick,验证调用时序

**③ 端到端冒烟(5-10 个,Playwright)**

- 打开 /dashboard → 4 市场卡片渲染
- /events 滚动加载、筛选生效
- /candidates 点开看到 reason_chain
- 模拟 adapter 故障 → UI 正确显示降级
- `make dev` 从零启动 → 2 分钟内 /dashboard 可用

**CI 策略**:只跑单元测试 + 前端 lint/build。集成和 e2e 走本地 `make test-full`,避免外部依赖把 CI 搞黄。

### 6.5 数据质量自检(每小时一次)

- 每市场最新 bar 的 ts 是否在预期窗口(A 股盘中 < 30s,Crypto < 5s)
- DuckDB 行数与 RawEvent 计数器差异
- 任意 symbol 近 24h 收盘价缺口超过 20% → WARN
- `analyzed_at IS NULL` 且 `ts < now - 10min` 的条目数 → 队列积压预警

异常写日志 + `/api/health.data_quality` 字段,不影响服务。

### 6.6 V1 不做的运维

- ❌ 高可用 / 多实例
- ❌ 备份自动化(用户自己备 `data/`)
- ❌ 告警通知(邮件/TG)—— v2
- ❌ 性能 profiling / APM

---

## 7. 技术栈与目录结构

### 7.1 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3.11+ / FastAPI / asyncio | 与 akshare、yfinance、tushare 生态一致 |
| 调度 | APScheduler | 单机够用,API 友好 |
| LLM | Ollama 本地 + OpenAI 兼容接口 | 默认本地零成本,可切云端 |
| 时序存储 | DuckDB | 列存、零依赖、SQL 友好 |
| 关系存储 | SQLite + JSON1 | 单文件、事务、JSON 查询 |
| 前端 | Next.js 14 App Router + TypeScript | 与 stockanalysis.com 风格一致,SSR 可选 |
| 图表 | TradingView Lightweight Charts | 开源、轻量、专业 K 线 |
| UI Kit | Tailwind + shadcn/ui | 快速搭可用界面 |
| 测试 | pytest / Playwright | 标准选型 |

### 7.2 目录结构(建议)

```
marketpulse/
├── Makefile                  # make dev / make test / make build
├── pyproject.toml
├── config/
│   ├── factors.yaml
│   ├── sources.yaml          # 数据源开关与凭据引用
│   └── llm.yaml
├── apps/
│   ├── api/                  # FastAPI 入口
│   │   ├── main.py
│   │   ├── routes/
│   │   └── ws/
│   └── web/                  # Next.js 前端
│       ├── app/
│       └── components/
├── core/
│   ├── adapters/
│   │   ├── ashare.py
│   │   ├── hk.py
│   │   ├── us.py
│   │   └── crypto.py
│   ├── factors/
│   │   ├── value.py
│   │   ├── momentum.py
│   │   ├── event.py
│   │   ├── risk.py
│   │   └── composer.py
│   ├── events/
│   │   ├── collectors/
│   │   ├── prefilter.py
│   │   ├── llm_analyzer.py
│   │   └── pipeline.py
│   ├── scheduler/
│   ├── persistence/
│   │   ├── duckdb_repo.py
│   │   └── sqlite_repo.py
│   └── domain/               # Bar / Quote / Event / BuyCandidate
├── data/                     # *.duckdb / *.db(运行时生成)
├── logs/
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

### 7.3 关键依赖列表

```toml
# pyproject.toml [project.dependencies]
fastapi
uvicorn[standard]
apscheduler
duckdb
aiosqlite
akshare
mootdx
yfinance
curl_cffi              # yfinance 反爬
alpaca-py
websockets             # Binance/OKX
httpx
structlog
pydantic
pydantic-settings
ollama                 # 本地 LLM 客户端
openai                 # 云端兼容
jieba
feedparser             # RSS
```

---

## 8. 路线图

### V1(本次实现范围)

- 4 个市场 Adapters + 备源
- 大盘 dashboard / 事件流 / Top-10 候选 / 设置页
- LLM 影响面 + 规则降级
- 多因子 + 一票否决
- `make dev` 一键启动

### V2(下一轮)

- 个股详情页(K 线 + 财务 + 事件时间线)
- 预测复盘:历史信号回测、命中率统计
- 告警通知:邮件 / TG / Webhook
- 简易回测框架(基于 vectorbt 或自研)

### V3+

- ML 选股(XGBoost / LightGBM)
- 模拟盘 / 持仓跟踪
- 多人协作(Auth + 关注列表共享)

---

## 9. 风险与开放问题

1. **akshare 接口稳定性**:东财接口偶发改版,需要在 Adapter 层做版本兼容与监控告警 → 通过备源 mootdx + 健康检查兜底
2. **Alpaca IEX 数据完整性**:免费层只覆盖 IEX 单一交易所,深度有限。验收只要求"延迟行情",可接受 → 文档明确说明,标注"非全市场行情"
3. **本地 LLM 响应时间**:Qwen2.5-7B 在 M1 Mac 单次推理约 2-5s。事件高峰可能积压 → 队列 + 降级机制兜底,云端配置项可手动切
4. **跨市场行业分类映射**:A 股用申万、HK 用恒生、US 用 GICS,Crypto 没有标准。V1 内每市场用各自分类,横向对比留 v2 → 文档已明确 V1 不做跨市场行业横向对比
5. **数据合规**:akshare 等聚合源实际是反爬抓取,商业使用有合规风险。本工具仅个人本地研究使用,不做对外服务
