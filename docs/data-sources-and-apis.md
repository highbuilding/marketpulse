# 数据接口全景 — 各市场用到的所有数据源与接口

> 2026-06-06 编制。覆盖 A股 / 美股 / Crypto / 港股 四市场的**全部外部数据接口**:
> 用途、数据源、走不走 ak_call 三层中间件、限频归属。代码事实(非记忆),
> 改采集前先查这张表。
>
> 三类调用通道:
> 1. **ak_call**(akshare 子进程隔离 + Breaker/Ratelimit/Outlet 三层中间件)— A股/港股大部分
> 2. **HTTP 直连**(httpx/requests,不经 ak_call)— A股 sina quote、crypto Binance、美股 Alpaca SDK
> 3. **WebSocket**(实时流)— crypto Binance WS、美股 Alpaca IEX WS

---

## 0. 限频归属速查(三个令牌桶 + 各源)

| source | 令牌桶 rate/burst | 覆盖的接口 | 限频中间件 |
|---|---|---|---|
| **sina** | 5/s, burst 20 | stock_zh_a_minute、stock_zh_a_daily、stock_zh_index_daily、fund_etf_*_sina、stock_zh_a_spot | ✅ ak_call |
| **em**(东财) | 10/s, burst 50 | stock_zh_a_spot_em、stock_*_fund_flow*、stock_hsgt_*、stock_board_*_name_em、stock_board_*_cons_em、stock_cyq_em、stock_hk_spot_em | ✅ ak_call |
| **ths**(同花顺) | 3/s, burst 10 | stock_board_*_cons_ths(仅低频校准备选) | ✅ ak_call |
| sina quote(hq.sinajs.cn) | 无中间件 | A股实时报价 HTTP 直连 | ❌ 自带 session |
| Alpaca | SDK 自限(~200/min) | 美股全部 | ❌ CircuitBreaker(adapter 内) |
| Binance | 无中间件 | crypto REST + WS | ❌ 走代理 |

> **关键**:5m 和 1d 虽是不同 akshare 函数,但**都归 source=sina**,共享同一个 5/s 令牌桶。
> 这是冷启动 sina 限频的根源(种子 + poller 并发挤爆同一个桶)。

---

## 1. A股(ashare)

### 1.1 实时报价 quote(10s,collector tick_snapshot)
| 接口 | 通道 | 用途 | 源 |
|---|---|---|---|
| `https://hq.sinajs.cn/list=` | **HTTP 直连**(requests session,GBK)| 主源:批量实时快照(价/量/买卖盘) | sina |
| mootdx(通达信)`Quotes.factory` | **本地库** | 备源:sina 失败时降级 | 通达信协议 |

`core/adapters/ashare.py::_fetch_snapshot_sina`(主)/ `_fetch_snapshot_mootdx`(备)。**不走 ak_call**,自带 5s 超时。

### 1.2 K 线历史/收线 — 全走 ak_call(source=sina/em)
| akshare 函数 | caller | 用途 | source | 备注 |
|---|---|---|---|---|
| `stock_zh_a_minute` | `ashare.fetch_intraday` | **5m/15m/30m/60m 分钟 K 线**(`getKLineData`,datalen=1970)| sina | 实时收线 + 种子 5m;**限频空体→IndexError** |
| `stock_zh_a_minute` | `ashare.fetch_intraday_raw` | qfq 当日因子未入表时,用 adjust='' 兜底 NaN | sina | 同上接口,adjust 不同 |
| `stock_zh_a_minute` | `index_minute.refresh` | 8 大指数 5m 序列(大盘 IndexCard)| sina | |
| `stock_zh_a_minute` | `baseline_persist.ashare` | 指数 prev_close 基线持久化 | sina | |
| `stock_zh_a_daily` | `ashare.fetch_history` | **个股日线 1d**(OHLCV)| sina | 种子 1d 主接口 |
| `stock_zh_a_hist` | `ashare.fetch_history`(:stock_metrics)| 补日线**成交额/换手率**(daily 缺字段时)| em | 低频补充 |
| `stock_zh_a_hist` | `ashare.fetch_history_tf`(:1wk)| 周线直拉(period=weekly)| em | **已弃用**:1wk 改 1d 聚合(单锚点) |
| `stock_zh_index_daily` | `ashare.fetch_history`(:index)| **指数日线**(000001.SH 等)| sina | |
| `fund_etf_hist_sina` | `ashare.fetch_history`(:etf)| **ETF 日线** | sina | |
| `fund_etf_category_sina` | `directory.refresh_ashare` | ETF 列表(symbol directory)| sina | 低频 |
| `stock_zh_a_spot` | `directory.refresh_ashare` | A股全市场列表(代码/名称)| sina | 低频 |
| `stock_zh_a_spot_em` | `market_query.all_ashare` | 全市场快照(涨跌幅榜 top)| em | |

### 1.3 资金流 / 筹码 / 板块 — ak_call(source=em/ths)
| akshare 函数 | caller | 用途 | source |
|---|---|---|---|
| `stock_cyq_em` | `chip.summary` | **筹码分布**(成本分位)| em |
| `stock_hsgt_fund_flow_summary_em` | `index_minute.fund_inflow` | 北向资金净流入(大盘)| em |
| `stock_hsgt_hist_em` | `fund_flow.pull_north` | 北向历史 | em |
| `stock_individual_fund_flow` | `fund_flow.pull_symbol` | 个股资金流 | em |
| `stock_board_industry_name_em` | `market_query.sectors_ashare` | 行业板块列表 | em |
| `stock_board_concept_name_em` | `market_query.sectors_ashare` | 概念板块列表 | em |
| `stock_board_industry_cons_em` | `market_query.sector_constituents` | 行业成分股 | em |
| `stock_board_concept_cons_em` | `market_query.sector_constituents` | 概念成分股 | em |

> 2026-06-14 验证:强制直连(`NO_PROXY='*'`)下,东财 `push2` 系板块/概念列表和全 A `clist/get` 均出现 `RemoteDisconnected`;sina quote 同环境 0.19s 正常。因此板块/概念/题材宇宙不能作为盘中实时依赖,应使用本地预固化 seed 作为 SSoT,东财/同花顺仅用于收盘后或手动低频校准。详见 `docs/2026-06-14-ashare-theme-data-source-feasibility.md`。

---

## 2. 美股(us)— Alpaca 主源 + yfinance 备源

**全部 HTTP 直连(Alpaca SDK / yfinance),不走 ak_call**;adapter 内自带 CircuitBreaker。
境内须走代理(`MARKETPULSE_PROXY_URL`)。

### 2.1 实时报价 quote(10s)
| 接口 | 通道 | 用途 | feed |
|---|---|---|---|
| `StockLatestQuoteRequest` / `get_stock_latest_quote` | Alpaca SDK | 主源:最新买卖报价 | — |
| `yf.Ticker(...).fast_info` | yfinance | 备源:Alpaca 失败降级 | — |

`core/adapters/us.py::_fetch_snapshot_alpaca`(主)/ `_fetch_snapshot_yfinance`(备)。

### 2.2 K 线历史/收线 — Alpaca StockBars(feed=sip 权威)
| Alpaca 请求 | 方法 | 用途 | TimeFrame |
|---|---|---|---|
| `StockBarsRequest` | `fetch_history` / `_fetch_history_alpaca` | **日线 1d**(2019至今,adjustment=all 前复权)| `TimeFrame.Day` |
| `StockBarsRequest` | `fetch_intraday` | **5m/15m/30m/60m**(intraday,仅 ~59 天)| `TimeFrame(5/15/30, Min)` / `Hour` |
| `StockBarsRequest` | `fetch_history_tf` | **60m/4h/1wk/1mo 深历史**(2019至今)| `Hour` / `(4,Hour)` / `(1,Week)` / `(1,Month)` |

- **feed=sip**:全市场权威成交量(收线/信号用,~15min 延迟)。
- 种子:1d + 60m + 4h 直拉(6年深度);5m 直拉(59天);15m/30m + 1wk/1mo 聚合。

### 2.3 实时 K 线 / 分时(WebSocket)
| 接口 | 通道 | 用途 | feed |
|---|---|---|---|
| `wss://stream.data.alpaca.markets/v2/iex` | Alpaca WS | **逐笔 trades** → 进行中 bar(final=false)+ 真 VWAP 分时图 | **IEX**(实时,仅 IEX 交所量,偏小) |

`apps/collector/us/ws_consumer.py`。**实时用 IEX**(无延迟但量偏小),**收线用 SIP**(权威)。两源差异见 CLAUDE.md「美股成交量分两源」。

---

## 3. Crypto — Binance(全程走代理)

**全部 HTTP/WS 直连,不走 ak_call**。境内 Binance 必须走代理。

| 接口 | 通道 | 用途 |
|---|---|---|
| `https://api.binance.com/api/v3/klines` | HTTP(httpx)| **历史 K 线回填**(backfill 到上市首日 2017)。8 周期逐页游标拉取 |
| `wss://stream.binance.com:9443/stream` | WebSocket | **实时 K 线 8 周期**(`@kline_{iv}`,推 final=false/true)。标杆实时源 |
| `wss://stream.binance.com:9443/ws` | WebSocket | 旧版单流(`@kline_1m`,core/adapters/crypto.py)|
| `https://api.coingecko.com/api/v3/simple/price` | HTTP | **已搁置**:Crypto IndexCard(coingecko 429 限频严重)|

`apps/collector/crypto/backfill.py`(REST 回填)+ `ws_consumer.py`(WS 实时)。
crypto K 线 ts 用 **open 对齐**(雷区 3 例外),全周期 WS 原生不聚合。

---

## 4. 港股(hk)— 部分实装

| akshare 函数 | caller | 用途 | source |
|---|---|---|---|
| `stock_hk_spot_em` | `market_query.top_hk` | 港股快照(涨跌幅榜)| em |
| `stock_hk_index_daily_em` | (映射存在)| 港股指数日线 | em |

**HK 指数 collector job 暂未实装**(`/api/indices/HSI.HK/minute` 返 stale)。HK K 线 60m/4h 接口未接(详情页 tab 隐藏)。

---

## 5. 速查:每个市场"取什么数据走什么"

| 数据 | A股 | 美股 | Crypto |
|---|---|---|---|
| **实时报价 quote** | sina HTTP `hq.sinajs.cn`(备 mootdx)| Alpaca SDK `latest_quote`(备 yfinance)| Binance WS |
| **5m 分钟 K 线** | `stock_zh_a_minute`(sina,ak_call)| Alpaca `StockBars`(SDK,59天)| Binance WS/REST |
| **日线 1d** | `stock_zh_a_daily`(sina,ak_call)| Alpaca `StockBars Day`(SDK,6年)| Binance WS/REST |
| **60m/4h** | 5m 聚合 | Alpaca 直拉(6年)| Binance WS |
| **1wk/1mo** | 1d 聚合(W-FRI/ME)| 1d 聚合(W-FRI/ME)| Binance WS 原生 |
| **实时 K 线进行态** | sina quote 驱动 ticker | Alpaca IEX WS 逐笔 | Binance WS |
| **分时图** | sina quote 累计额 | Alpaca IEX WS 真 VWAP | (不做)|
| **资金流/筹码** | em/ths(ak_call)| — | — |
</content>
