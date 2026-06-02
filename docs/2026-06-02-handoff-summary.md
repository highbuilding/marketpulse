# 交接总结(2026-06-02)

> 本轮工作的总览,给接手的人。详细设计/计划/审计见各自文档(下方"文档地图")。

## 一、本轮交付了什么

### 1. 美股实时 K 线 + 券商口径分时图(对齐 A 股/crypto)
- WS 从 `bars`(1m)换成 **`trades`(逐笔)**;`TradeHub` 内存累加 + ~1s 节流 → 进行中态 ticker(`final=false`)+ 真 VWAP 分时图。
- 收线走 **REST SIP**(权威成交量),桶滚动 provisional 仅发 bus 填 SIP 延迟洞;**1m 不再落库**。
- 美股冬令时盘后桶错位修复(`current_bucket` 用市场本地日)。
- 设计:`docs/superpowers/specs/2026-06-01-realtime-kline-and-intraday-us.md`;计划:`docs/superpowers/plans/2026-06-01-realtime-kline-and-intraday-us.md`。

### 2. 全工程审计 + bug 修复
- 审计:`docs/2026-06-01-collection-db-backfill-audit.md`(采集矩阵/DB/前端SSE/启动回填/bug 清单)。
- 已修:B4(美股 1m 孤儿,守卫+清 2431 行)、B5(A股 15m/30m 双源竞态→单一直取,含 sweep)、B9(删遗留库 ~187M)、存储边界策略(1m+进行中态不存、5m+收线存)。

### 3. P0 核心标的 baseline + 启动 reconcile
- `core/domain/core_symbols.py`(CORE 单一事实源);signal/fetch cron + 美股 poller + sweep + `tick_snapshot` 全部并入 `CORE∪watchlist`(修 B1/B2/B7)。
- `apps/collector/startup_reconcile.py`:开机 gap 检测回补(冷启动填史 + kill 后补断档);warm restart 零外部调用(防打爆数据源)。
- 计划:`docs/superpowers/plans/2026-06-02-core-symbols-baseline-and-startup-reconcile.md`。

### 4. 多用户化(~100 并发 / 邀请制 VPS)—— 5 个工作包全交付
- 评审:`docs/2026-06-02-multiuser-vps-scalability-review.md`(瓶颈 W1-W11 + 附录 A refill 放大详析)。
- 设计:`docs/superpowers/specs/2026-06-02-multiuser-scaling-design.md`。
- **① 安全闸**:口令 cookie 鉴权(`apps/api/auth.py`,**无 `APP_SECRET` 时自动关闭**,dev 不受影响)+ refill 白名单/去重 + `deploy/nginx.conf`/`deploy/README.md`。
- **② SSE hub**:`apps/api/sse_hub.py` 单读多分发(O(用户×消息)→O(消息))+ 背压(丢最旧)+ finally 注销 + 订阅每 60s 续期。
- **③ 多 worker**:Redis 池 `max_connections=50`;SSE hub 已 per-worker;生产启动/nginx 见 deploy/。
- **⑤ 美股 LRU-30**:`state:us:viewed` ZSET 最近访问 → trades 订阅 top-30 + `state:us:realtime_active` + 前端"超出实时名额"提示。
- **④ 读缓存**:历史分页 Redis 页缓存(游标页长 TTL/最新页短 TTL)。
- 计划:`docs/superpowers/plans/2026-06-02-multiuser-p1-security-gate.md`、`...-p2-sse-hub.md`、`...-p345-workers-lru-readcache.md`。

### 5. 前端实时性修复
- 首页指数卡从 `bars/history?1d`(盘中不动)改 `/api/indices/{s}/minute`(今日实时涨跌幅,30s 轮询;crypto 回退 1d)。
- ticker/writer `set_msgpack ttl=`→`ttl_s=`(4 处,修 `:current` 写失败 + 日志刷屏)。

---

## 二、当前运行方式

- 本地:redis(docker-compose)+ 3 collector + api(`uvicorn --port 8787`)+ web(`next dev` 3000)。**dev 不设 `APP_SECRET` → 鉴权关闭,行为不变**。
- 生产:见 `deploy/README.md`(设 `APP_SECRET`/`APP_PASSCODES`,api `--workers N`,`next build/start`,nginx TLS+HTTP2+限流)。

---

## 三、已知问题 / 待办(本轮未处理,留给后续)

| 项 | 说明 | 优先级 |
|---|---|---|
| **B-sina** | sina 数据源反复封 IP(`stock_zh_a_minute` 返回 banned/空 → 熔断反复开),A 股实时/分时间歇中断。**环境性,非代码 bug**。根治需 em/ths 兜底或代理池 | 🔴高 |
| 300059(东方财富)5m | 实时价已好;5m K线/涨跌幅被 B-sina 挡;早先有一次 sina 对它返回空(0,0),待 sina 健康时复验是否其专属符号问题 | 🟡中 |
| 指数卡仍是 pull(30s) | 自选是 SSE push(~10s),指数卡是 30s 轮询。可升级为 SSE 实时价 + 低频拉 extras | 🟢低 |
| 读缓存缓存空结果 | ④ 页缓存对空结果也缓存(latest 30s),symbol 无数据时短暂多缓存一次空,可加"非空才缓存" | 🟢低 |
| B3 派生中段缺口 | reconcile 全量重聚合已缓解;sweep 的 `_decide_window` 仍有中段盲区 | 🟡中 |
| B6 crypto gap 7天窗 | gap 检测只看最近 7 天,超 7 天中段断档检测不到;需"检测全部缺口+逐段精确回补"或网格 diff | 🟡中 |
| `test_index_minute.py` 2 例失败 | 时间漂移的测试 fixture(写死日期超出 cutoff),**预存、非本轮引入**,与生产无关 | 🟢低 |
| 美股 1d 无进行中态 | A股/美股 1d/1wk/1mo 无实时跳(crypto 有),设计取舍 | 🟢低 |

---

## 四、完整改动清单(看"全部改动点")

> 本文档(§一)是按模块的高层总结。**要逐处看,用 git** —— 本 epic 相对上一基线(`origin/master` = `779cab0`)共 **84 commits、84 文件、+3732/-368**。

**看全量的命令**:
```bash
git log --oneline origin/master..HEAD              # 全部 84 个 commit(每个含改动点描述)
git diff --stat origin/master..HEAD                # 文件级统计
git diff origin/master..HEAD -- <某文件>           # 看具体某文件的全部改动
```
每个特性的"逐文件 + 逐步骤"在对应 plan 的「文件结构 / Task」里(`docs/superpowers/plans/`)。

### 新增模块(非测试)
- **core**:`core/domain/core_symbols.py`(CORE 单一事实源)、`core/domain/bucket_state.py`(共享桶纯函数)、`core/persistence/intraday_repo.py`(分时独立库)
- **collector**:`apps/collector/startup_reconcile.py`(启动回填);A股 `ashare/quote_bar_ticker.py`、`ashare/intraday_line_writer.py`;美股 `us/trade_hub.py`、`us/bar_ticker.py`、`us/bar_poller.py`、`us/intraday_line_writer.py`
- **api**:`apps/api/auth.py`(鉴权)、`apps/api/sse_hub.py`(SSE 单读多分发)、`apps/api/routes/sse_intraday.py`(分时 SSE)
- **前端**:`apps/web/app/login/page.tsx`、`apps/web/components/IntradayLineChart.tsx`、`apps/web/lib/use_intraday_line.ts`、`apps/web/lib/api_fetch.ts`
- **部署**:`deploy/nginx.conf`、`deploy/README.md`
- **测试**:~38 个新单测(`tests/unit/**`,覆盖以上各模块)

### 改动的关键文件(非新增)
- **api**:`main.py`(挂 auth 中间件 + SSE hub lifespan)、`routes/sse_bars.py`(改 hub + 订阅续期 + viewed ZADD)、`routes/symbols.py`(refill 防护 + 历史读缓存 + 分时 realtime flag)
- **collector**:`ashare/main.py`、`us/main.py`(接线 ticker/writer/poller/reconcile/purge)、`ashare/bar_poller.py`(砍1m/收线/B5)、`us/ws_consumer.py`(trades + LRU-30)、`jobs/aggregate_derived.py`(事件驱动 + B5)、`base.py`(分时只读路由 + 昨收)
- **core**:`scheduler/jobs.py`(tick_snapshot 并 CORE + 砍伪 bar)、`scheduler/signal_jobs.py`(cron 并 CORE)、`scheduler/scheduler.py`、`cache/keys.py`(分时/页缓存 key)、`cache/redis_client.py`(池上限)、`persistence/duckdb_repo.py`(1m 守卫)、`domain/{market_sessions,intervals,models}.py`、`adapters/ashare.py`(amount)
- **前端**:`app/page.tsx`(指数卡实时 + 自选 SSE)、`app/symbol/[code]/page.tsx`(分时/K线切换 + 非RTH默认)、`lib/{markets,intervals}.ts`

---

## 五、文档地图

- 采集/DB/回填审计:`docs/2026-06-01-collection-db-backfill-audit.md`
- 多用户 VPS 评审:`docs/2026-06-02-multiuser-vps-scalability-review.md`
- 设计 specs:`docs/superpowers/specs/2026-06-0{1,2}-*.md`
- 实施 plans:`docs/superpowers/plans/2026-06-0{1,2}-*.md`
- 部署:`deploy/README.md` + `deploy/nginx.conf`
- 项目入门:`CLAUDE.md`(已更新三市场实时落地态)
