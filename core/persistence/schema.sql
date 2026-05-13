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
