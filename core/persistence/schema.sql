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

-- A 股涨停/炸板/跌停池事实源。
-- pool_type: limit_up / broken_limit / down_limit。
-- trade_date 用 BJT 交易日 YYYY-MM-DD; pulled_at 是采集 UTC ISO。
CREATE TABLE IF NOT EXISTS limit_pool_daily (
  market TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  pool_type TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT,
  change_pct REAL,
  price REAL,
  limit_price REAL,
  amount REAL,
  free_float_cap REAL,
  total_cap REAL,
  turnover_rate REAL,
  seal_amount REAL,
  first_seal_time TEXT,
  last_seal_time TEXT,
  break_count INTEGER,
  ladder_count INTEGER,
  industry TEXT,
  amplitude REAL,
  raw_json TEXT NOT NULL DEFAULT '{}',
  pulled_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, trade_date, pool_type, symbol)
);
CREATE INDEX IF NOT EXISTS idx_limit_pool_date_type
  ON limit_pool_daily(market, trade_date, pool_type);
CREATE INDEX IF NOT EXISTS idx_limit_pool_symbol_date
  ON limit_pool_daily(market, symbol, trade_date DESC);

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

-- Plan: A 股 AI 盘中决策助手
-- 用户手动维护持仓/观察记录。删除用 status='closed' 软删除, 保留复盘依据。
-- 同一标的可多条记录(多次开/平仓), 按 id 操作; 无 UNIQUE(market,symbol) 约束。
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT,
  quantity INTEGER NOT NULL DEFAULT 0,
  cost_price REAL,
  close_price REAL,
  profit_amount REAL,
  profit_pct REAL,
  opened_at TIMESTAMP,
  closed_at TIMESTAMP,
  strategy_tag TEXT,
  entry_reason TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  note TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_market_status
  ON positions(market, status, updated_at DESC);

-- 题材/板块快照: collector 写, API/Web 只读。
-- 题材静态维护表: 内置 seed + 用户手工调整后的 SSoT。
-- theme_snapshots/theme_memberships 只放盘中动态计算结果, 不承担静态成分股事实源。
CREATE TABLE IF NOT EXISTS theme_universe (
  market TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  theme_name TEXT NOT NULL,
  classification TEXT NOT NULL,   -- 'index_weight' | 'industry' | 'concept' | 'theme' | 'watch'
  priority TEXT NOT NULL DEFAULT 'P2',
  enabled INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'manual',
  seed_version TEXT,
  note TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, theme_code)
);
CREATE INDEX IF NOT EXISTS idx_theme_universe_market_priority
  ON theme_universe(market, enabled, priority, updated_at DESC);

CREATE TABLE IF NOT EXISTS theme_constituents (
  market TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT,
  role_hint TEXT,
  weight REAL,
  enabled INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'manual',
  seed_version TEXT,
  note TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, theme_code, symbol),
  FOREIGN KEY (market, theme_code) REFERENCES theme_universe(market, theme_code) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_theme_constituents_symbol
  ON theme_constituents(market, symbol, enabled);

-- 采集标的清单: A 股采集任务唯一事实源。
-- 题材库/自选股只能从这里选择, 不反向决定采集范围。
CREATE TABLE IF NOT EXISTS collector_symbols (
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'manual', -- 'core' | 'seed' | 'manual'
  collect_snapshot INTEGER NOT NULL DEFAULT 1,
  collect_5m INTEGER NOT NULL DEFAULT 1,
  collect_signals INTEGER NOT NULL DEFAULT 1,
  note TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, symbol)
);
CREATE INDEX IF NOT EXISTS idx_collector_symbols_market_enabled
  ON collector_symbols(market, enabled, collect_snapshot, collect_5m, updated_at DESC);

-- 实盘消息事实源: Redis Stream 只做实时分发,这里用于复盘和 AI 上下文。
CREATE TABLE IF NOT EXISTS live_messages (
  id TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  level TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  theme_code TEXT,
  symbol TEXT,
  symbols_json TEXT NOT NULL DEFAULT '[]',
  source_event TEXT NOT NULL,
  source_event_id TEXT,
  dedupe_key TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  rule_version TEXT NOT NULL DEFAULT 'v1',
  created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_messages_market_ts
  ON live_messages(market, ts DESC);
CREATE INDEX IF NOT EXISTS idx_live_messages_category_ts
  ON live_messages(market, category, ts DESC);
CREATE INDEX IF NOT EXISTS idx_live_messages_theme_ts
  ON live_messages(market, theme_code, ts DESC);
CREATE INDEX IF NOT EXISTS idx_live_messages_symbol_ts
  ON live_messages(market, symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_live_messages_dedupe_ts
  ON live_messages(market, dedupe_key, ts DESC);

CREATE TABLE IF NOT EXISTS theme_snapshots (
  market TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  theme_name TEXT NOT NULL,
  classification TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  pct_change REAL,
  pct_change_5m REAL,
  amount REAL,
  amount_ratio REAL,
  up_ratio REAL,
  limit_up_count INTEGER,
  member_count INTEGER,
  leader_symbols_json TEXT NOT NULL DEFAULT '[]',
  divergence_score REAL,
  support_score REAL,
  raw_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, theme_code, ts)
);
CREATE INDEX IF NOT EXISTS idx_theme_snapshots_market_ts
  ON theme_snapshots(market, ts DESC);

-- 题材状态机当前结果。
CREATE TABLE IF NOT EXISTS theme_states (
  market TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  theme_name TEXT NOT NULL,
  state TEXT NOT NULL,
  score REAL,
  reason TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, theme_code)
);
CREATE INDEX IF NOT EXISTS idx_theme_states_market_state
  ON theme_states(market, state, updated_at DESC);

-- 题材成分股角色。role 由 RoleResolver 生成。
CREATE TABLE IF NOT EXISTS theme_memberships (
  market TEXT NOT NULL,
  theme_code TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT,
  role TEXT,
  pct_change REAL,
  amount REAL,
  volume_ratio REAL,
  is_above_intraday_avg INTEGER,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, theme_code, symbol)
);
CREATE INDEX IF NOT EXISTS idx_theme_memberships_symbol
  ON theme_memberships(market, symbol);

-- 程序生成的事实事件, 不放 AI 长结论。
CREATE TABLE IF NOT EXISTS market_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_events_market_time
  ON market_events(market, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_events_subject
  ON market_events(market, subject_type, subject_id, occurred_at DESC);

-- 策略规则生成候选, AI 基于候选和证据给观察结论。
CREATE TABLE IF NOT EXISTS trade_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  candidate_key TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT,
  theme_code TEXT,
  theme_name TEXT,
  candidate_type TEXT NOT NULL,
  decision TEXT NOT NULL,
  score REAL NOT NULL,
  reasons_json TEXT NOT NULL DEFAULT '[]',
  risks_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  generated_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  UNIQUE(market, candidate_key)
);
CREATE INDEX IF NOT EXISTS idx_trade_candidates_market_status
  ON trade_candidates(market, status, score DESC, generated_at DESC);

-- 申万一级行业指数日线 (index_hist_sw, 经 collector ak_call 拉取)。
-- 行业指数仅 31×~1600 天, 量小, 存 SQLite 让 api 可直读 (不碰雷区6 DuckDB 互斥)。
-- trade_date 用自然日 YYYY-MM-DD; industry_code 形如 801010 (不带 .SI 后缀)。
CREATE TABLE IF NOT EXISTS sw_industry_index (
  market TEXT NOT NULL DEFAULT 'ashare',
  industry_code TEXT NOT NULL,
  industry_name TEXT,
  trade_date TEXT NOT NULL,
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  volume REAL,
  amount REAL,
  pulled_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, industry_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_sw_industry_index_date
  ON sw_industry_index(market, trade_date);

-- 申万一级行业最新元信息 (sw_index_first_info): 成份数 / 估值。
CREATE TABLE IF NOT EXISTS sw_industry_info (
  market TEXT NOT NULL DEFAULT 'ashare',
  industry_code TEXT NOT NULL,
  industry_name TEXT NOT NULL,
  member_count INTEGER,
  pe_static REAL,
  pe_ttm REAL,
  pb REAL,
  dividend_yield REAL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, industry_code)
);

-- 每日复盘结论快照: collector 收盘后生成 (日线分析在 collector 持 BarRepo 算),
-- 落 SQLite 供 api 只读 + 前端翻历史, 历史日由 backfill 脚本批量灌。
-- sections_json 是结论 section 列表序列化; 日线层 (走势/板块位置/龙头分层)
-- 全年每个交易日可算, 消息层仅近期有 (历史标 data_gaps)。
CREATE TABLE IF NOT EXISTS daily_reviews (
  market TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  formula_version TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  sections_json TEXT NOT NULL DEFAULT '[]',
  next_watch_json TEXT NOT NULL DEFAULT '[]',
  data_gaps_json TEXT NOT NULL DEFAULT '[]',
  generated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (market, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_reviews_market_date
  ON daily_reviews(market, trade_date DESC);

-- AI 交易观察结论。不是下单指令, 只提供结论和证据。
CREATE TABLE IF NOT EXISTS ai_trade_opinions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  opinion_key TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  target_name TEXT,
  decision TEXT NOT NULL,
  confidence REAL,
  title TEXT NOT NULL,
  thesis TEXT NOT NULL,
  reasons_json TEXT NOT NULL DEFAULT '[]',
  risks_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  source_candidate_id INTEGER,
  generated_at TIMESTAMP NOT NULL,
  expires_at TIMESTAMP,
  status TEXT NOT NULL DEFAULT 'active',
  UNIQUE(market, opinion_key)
);
CREATE INDEX IF NOT EXISTS idx_ai_trade_opinions_market_status
  ON ai_trade_opinions(market, status, generated_at DESC);
