# MarketPulse

本地运行的四市场行情监控分析平台。详见 `docs/superpowers/specs/2026-05-13-marketpulse-design.md`。

## 启动

### 首次安装

```bash
make install     # 安装 Python 依赖
make web-install # 安装前端依赖
cp .env.example .env  # 按需填入 API key
```

### 日常后台启动(推荐)

```bash
make dev-bg      # 后台启动 Redis + API + Web + 3 个 collector
make dev-status  # 检查进程与健康接口
make dev-stop-bg # 停止后台进程和 Redis
```

单独启停某个进程:

```bash
make dev-bg ARGS="collector-us"              # 只启动美股采集
make dev-stop-bg ARGS="collector-us"         # 只停止美股采集, 不动 Redis
bash scripts/dev-stop.sh collector-us        # 只停止美股采集, 不动 Redis
bash scripts/dev-stop.sh collector-crypto    # 只停止加密采集, 不动 Redis
bash scripts/dev-status.sh collector-ashare  # 只检查 A 股采集
```

打开 http://localhost:3000/dashboard。

后台启动日志:

```bash
tail -f /tmp/marketpulse/api.log
tail -f /tmp/marketpulse/web.log
tail -f /tmp/marketpulse/collector-ashare.log
```

结构化长期日志:

```bash
tail -f data/logs/api.log
tail -f data/logs/collector_ashare.log
tail -f data/logs/api-errors.log
tail -f data/logs/collector_ashare-errors.log
```

### 前台开发模式

```bash
make dev         # 用 honcho 前台启动, Ctrl-C 停止; 适合改代码时看滚动日志
```

## Plan 2 新增功能(基建夯实)

- `/symbol/[code]` —— 个股详情页(K 线 + 资金流;支持日/周/月/分时切换)
- `/watchlist` —— 自定义关注列表
- `/sector/[name]` —— 板块详情(成分股列表)

### 首次回填历史 K 线

```bash
make warmup                                          # 回填关注列表中所有 symbol 的近 1 年日线
. .venv/bin/activate && python -m apps.warmup --symbols 600519.SH,000858.SZ --days 90
```

### Plan 2 API 速查

| 路径 | 说明 |
|---|---|
| `GET /api/symbols/{sym}/bars?interval=1d&days=365` | K 线(支持 1d/1wk/1mo/1m/5m/15m/30m/60m) |
| `GET /api/symbols/{sym}/fund_flow?days=30` | 个股资金流时间序列 |
| `GET /api/sectors/list` | 板块列表 |
| `GET /api/sectors/{name}/constituents` | 板块成分股 |
| `GET /api/sectors/{name}/fund_flow?days=30` | 板块资金流 |
| `GET /api/watchlists` `POST` `PATCH` `DELETE` | 关注列表 CRUD |
| `GET /api/watchlists/{id}/symbols` `POST` `DELETE` | 关注列表成员 CRUD |
| `GET /api/north_flow?days=30` | 北向资金 |

### 调度器 Plan 2 Jobs

- 每分钟:`pull_north_flow_job`(北向资金)
- 每 30 分钟:`pull_watchlist_symbol_flow_job`(关注列表里的个股资金流)
- 每日 09:25 UTC:`refresh_sectors_job`(刷新所有新浪行业板块成分)
- 每日 02:00 UTC:`purge_fund_flow_job`(清理过期资金流数据)
