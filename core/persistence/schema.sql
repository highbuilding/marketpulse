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
