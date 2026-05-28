-- v1 schema
CREATE TABLE IF NOT EXISTS health_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TIMESTAMP NOT NULL,
  component TEXT NOT NULL,
  state TEXT NOT NULL,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_ts ON health_log(ts DESC);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

-- Plan 2: 自定义关注
CREATE TABLE IF NOT EXISTS watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  is_archived INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist_items (
  watchlist_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  added_at TIMESTAMP NOT NULL,
  PRIMARY KEY (watchlist_id, symbol),
  FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE
);

-- Plan 2: 资金流
CREATE TABLE IF NOT EXISTS fund_flow_symbol (
  symbol TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  main_net REAL,
  super_large_net REAL,
  large_net REAL,
  medium_net REAL,
  small_net REAL,
  PRIMARY KEY (symbol, ts)
);

CREATE INDEX IF NOT EXISTS idx_ff_symbol_ts ON fund_flow_symbol(ts DESC);

CREATE TABLE IF NOT EXISTS fund_flow_sector (
  sector_name TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  main_net REAL,
  pct_change REAL,
  PRIMARY KEY (sector_name, ts)
);

CREATE INDEX IF NOT EXISTS idx_ff_sector_ts ON fund_flow_sector(ts DESC);

CREATE TABLE IF NOT EXISTS fund_flow_north (
  ts TIMESTAMP PRIMARY KEY,
  hgt_net REAL,
  sgt_net REAL,
  total_net REAL
);

-- Plan 2.1: symbol directory (code + name)
CREATE TABLE IF NOT EXISTS symbol_directory (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  market TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbol_name ON symbol_directory(name);

-- Plan: 指标信号
-- 离散事件表(每根 K 线最多 1 条同向信号, UNIQUE 让 scan 幂等)
CREATE TABLE IF NOT EXISTS indicator_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,        -- '60m' | '4h' | '1d'
  indicator TEXT NOT NULL,       -- 'CD'(为 TT/NX 留扩展)
  signal_type TEXT NOT NULL,     -- 'buy' | 'sell'
  bar_ts TIMESTAMP NOT NULL,     -- 触发信号的 K 线时间(UTC ISO)
  detected_at TIMESTAMP NOT NULL,
  price REAL NOT NULL,
  d_value REAL,                  -- MACD DIF, 调试用
  acknowledged INTEGER NOT NULL DEFAULT 0,
  UNIQUE(symbol, interval, indicator, signal_type, bar_ts)
);

CREATE INDEX IF NOT EXISTS idx_sig_symbol_interval
  ON indicator_signals(symbol, interval, bar_ts DESC);
CREATE INDEX IF NOT EXISTS idx_sig_unack
  ON indicator_signals(detected_at DESC) WHERE acknowledged = 0;

-- Plan: CD 信号汇总通知
-- 收件人列表(按市场分组, channel 预留 wechat 扩展)
CREATE TABLE IF NOT EXISTS notification_recipients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,                     -- 'ashare' | 'us' | 'hk' | 'crypto'
  channel TEXT NOT NULL,                    -- 'email' | 'wechat'
  address TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL,
  UNIQUE(market, channel, address)
);
CREATE INDEX IF NOT EXISTS idx_recipients_market ON notification_recipients(market, enabled);

-- 每个 symbol 启用的 interval 列表(JSON array, 1d 必有, 服务端强制注入)
CREATE TABLE IF NOT EXISTS symbol_notification_config (
  symbol TEXT PRIMARY KEY,
  intervals_json TEXT NOT NULL DEFAULT '["1d"]',
  updated_at TIMESTAMP NOT NULL
);

-- 推送审计 + snapshot hash 去重
CREATE TABLE IF NOT EXISTS notification_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  triggered_at TIMESTAMP NOT NULL,
  snapshot_hash TEXT NOT NULL,              -- (symbol,interval,type,count) 排序后 sha256
  sent INTEGER NOT NULL,                    -- 1=发送, 0=与上轮一致跳过
  recipients_count INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_market_time ON notification_audit(market, triggered_at DESC);

-- A 股日线筹码分布摘要(东方财富 stock_cyq_em)
CREATE TABLE IF NOT EXISTS chip_summary (
  symbol TEXT NOT NULL,
  trade_date TIMESTAMP NOT NULL,
  profit_ratio REAL,
  avg_cost REAL,
  cost_90_low REAL,
  cost_90_high REAL,
  concentration_90 REAL,
  cost_70_low REAL,
  cost_70_high REAL,
  concentration_70 REAL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_chip_symbol_date
  ON chip_summary(symbol, trade_date DESC);

-- 大盘"今日成交额 vs 同时段基线"基线表 (2026-05-28 设计)
-- 每日收盘后 cron 写当日 5min 累计成交额曲线; 次日盘中查同 ts_5m_offset 算 amount_ratio
-- crypto 不入表 (Binance 24h ticker 现成)
CREATE TABLE IF NOT EXISTS market_amount_baseline (
  market        TEXT NOT NULL,        -- 'ashare' / 'hk' / 'us'
  trading_date  TEXT NOT NULL,        -- 'YYYY-MM-DD' 本市场所在地自然日
  ts_5m_offset  INTEGER NOT NULL,     -- 当日开盘后第 N 个 5min 桶 (0-based)
  cum_amount    REAL NOT NULL,        -- 累计成交额 (原始单位: 元 / 港元 / USD)
  PRIMARY KEY (market, trading_date, ts_5m_offset)
);
CREATE INDEX IF NOT EXISTS idx_baseline_market_date
  ON market_amount_baseline(market, trading_date DESC);
