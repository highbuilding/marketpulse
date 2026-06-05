# 环境分层(test/线上)+ 标的集扩充 + 数据清洗重跑 — 设计文档

> 日期:2026-06-05 · 状态:待审批
> 性质:部署治理改造。标的集按环境分层(test 精简 / 线上 ~400 主流),数据清空重跑,部署流程固化。crypto 不动。

---

## 0. 目标

1. **标的集分层**:`APP_ENV` 选 test/prod 两套 CORE 字典。本地=test,线上=prod。
2. **test 标的**:每市场扩到 **30 个**(大盘指数 + 热门个股凑满 30;crypto 除外,仍 5 币)。
3. **线上标的**:~400 —— A股沪深300 + 美股标普100 + 大盘指数。
4. **数据清洗**:清空 A股+美股全部 bar,按新采集模型(5m+1d 直取/其余聚合)全重跑。crypto 保留。
5. **部署流程**:test(本地)/ 线上 两套,固化成文档。
6. **调用量**:线上 ~300 A股标的受 sina 5/s 限频约束,轮询间隔须拉长。

---

## 1. 环境分层机制(APP_ENV 选两套字典)

`core/domain/core_symbols.py`:
```python
import os
CORE_TEST: dict[str, list[str]] = {
    "ashare": [8大指数 + 7~10 热门个股],   # ≈ 现有 CORE
    "us": [3 ETF + AAPL/NVDA/... 头部],
    "crypto": [BTC/ETH/SOL/XRP/TRX],
    "hk": [],
}
CORE_PROD: dict[str, list[str]] = {
    "ashare": [8大指数 + 沪深300 成分],     # ~308
    "us": [3 ETF + 标普100 成分],           # ~103
    "crypto": [同 test 5 币],
    "hk": [],
}
_ENV = os.getenv("APP_ENV", "test")
CORE_SYMBOLS = CORE_PROD if _ENV == "prod" else CORE_TEST

def core_symbols(market: str) -> list[str]:
    return list(CORE_SYMBOLS.get(market, []))
```
- 本地不设 APP_ENV → 默认 test;线上 `export APP_ENV=prod`。
- 沪深300/标普100 成分清单:用静态清单文件(`core/domain/_hs300.py` / `_sp100.py`,代码内常量,避免运行时拉取),实施时填入成分股代码。

## 2. 调用量与轮询间隔(线上硬约束)

- sina 限频 rate=5/s burst=20;`stock_zh_a_minute` 单标的无批量。
- 线上 A股 ~308 标的,5m 每标的 1 请求:
  - 现状 `POLL_INTERVAL_S=10` → 30.8 req/s,**超限 6 倍,不可行**。
  - **改为按环境分级**:test `POLL_INTERVAL_S=10`(标的少无压力);prod **`POLL_INTERVAL_S=60`**(308÷60≈5 req/s,卡限频)或更安全 `90`(≈3.4/s)。
  - 5m bar 每 5 分钟才收线,60-90s 轮询不影响及时性(收线后最多延迟 1 个轮询周期)。
- 1d:低频 cron,无压力。
- 美股 ~103:SIP 60s 轮询,Alpaca 限频宽松,压力小。
- **决策**:`POLL_INTERVAL_S` 按 APP_ENV 取值(test=10,prod=90)。

### 2.1 90s 轮询不影响分时图/进行态(已核实)
三条链路独立,分时与进行态走 quote(10s)、与 bar_poller 的 5m 轮询无关:
| 数据 | 驱动源 | 频率 | 受 90s 影响 |
|---|---|---|---|
| 分时图(时分线) | quote(intraday_line_writer 10s) | 10s | 否 |
| 进行态 K线(final=false) | quote(quote_bar_ticker 10s) | 10s | 否 |
| 5m 收线根(final=true) | bar_poller | prod 90s | 收线根晚 ~90s 入库 |
- 唯一影响:一根 5m bar 收线后最多晚 ~90s 落库;这 90s 内进行态由 quote_bar_ticker 实时跳动填充,用户看图不空。分时图照常 10s 更新。

## 3. 数据清洗重跑

- **清空**:A股 + 美股的 `bars_ashare.duckdb` / `bars_us.duckdb` 全部 bar(保留 crypto)。
- **备份**:清空前备份两个 duckdb。
- **重跑**:重启 collector(APP_ENV 对应环境)→ startup_reconcile + sweep 全量初始化按新模型(5m+1d 直取,15m/30m/60m/4h 从 5m 聚合,1wk/1mo 从 1d)重建。
- **信号**:bar 重建后,事件驱动 scan + 补扫重算 CD 信号。
- test 环境(本地)先做;线上环境部署时同样流程。

## 4. 部署流程(固化)

### test(本地)
```
不设 APP_ENV(默认 test)
make dev / 雷区2 模板启 3 collector + api + web + redis
标的 = CORE_TEST(精简集)
```
### 线上(prod)
```
export APP_ENV=prod
设 APP_SECRET/APP_PASSCODES(鉴权)、SMTP_*(邮件)
3 collector + api(--workers N)+ web(build/start)+ redis + nginx(deploy/nginx.conf)
标的 = CORE_PROD(~400)
POLL_INTERVAL_S=90(env 内生效)
```
- deploy/README.md 补充:APP_ENV 说明 + 标的分层 + 调用量注意。

## 5. 改动范围

**改动**:
- `core/domain/core_symbols.py`:CORE_TEST/CORE_PROD + APP_ENV 选择
- `core/domain/_hs300.py`、`_sp100.py`(新增):成分股静态清单
- `apps/collector/ashare/bar_poller.py`:POLL_INTERVAL_S 按 APP_ENV 分级
- `deploy/README.md`:部署流程 + APP_ENV

**数据操作**(一次性):清空 A股+美股 bar + 重跑(备份先行)。

**不碰**:crypto、采集模型逻辑(上一轮已重构)、api 读路径、前端、DB schema。

## 6. 风险与回滚

- **B-sina 限频**:线上 90s 轮询卡在安全线;若仍超限,继续拉长或缩小 prod 标的集。
- **清空数据不可逆**:清空前备份 duckdb;crypto 不动。
- **沪深300/标普100 成分变动**:静态清单需定期手动维护(本轮一次性填入,后续手动更新)。
- 回滚:APP_ENV 切回 test 即用精简集;清空有备份可恢复。

## 7. 待实施时定的小项
- 沪深300/标普100 具体成分清单(实施时拉取最新成分填入静态文件)。
- test 热门个股具体名单(建议 = 现有 CORE 的 7 个股)。
- 线上 POLL_INTERVAL_S 最终值(90 起,按实际限频微调)。
