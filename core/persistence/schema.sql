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

-- Plan 2: 板块
CREATE TABLE IF NOT EXISTS sectors (
  name TEXT PRIMARY KEY,
  classification TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS sector_constituents (
  sector_name TEXT NOT NULL,
  symbol TEXT NOT NULL,
  PRIMARY KEY (sector_name, symbol)
);

CREATE INDEX IF NOT EXISTS idx_sector_const_symbol ON sector_constituents(symbol);

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
