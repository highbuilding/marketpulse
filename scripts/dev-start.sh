#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${MARKETPULSE_LOG_DIR:-/tmp/marketpulse}"
mkdir -p "$LOG_DIR"

cd "$ROOT"

if [[ ! -d ".venv" ]]; then
  echo "missing .venv; run: make install"
  exit 1
fi

if [[ ! -d "apps/web/node_modules" ]]; then
  echo "missing apps/web/node_modules; run: make web-install"
  exit 1
fi

echo "starting redis..."
docker compose -f docker-compose.dev.yml up -d redis >/dev/null

selected=("${@:-all}")

should_start() {
  local name="$1"
  for item in "${selected[@]}"; do
    if [[ "$item" == "all" || "$item" == "$name" ]]; then
      return 0
    fi
  done
  return 1
}

start_proc() {
  local name="$1"
  local pattern="$2"
  local cmd="$3"
  local log_file="$LOG_DIR/${name}.log"

  if pgrep -f "$pattern" >/dev/null 2>&1; then
    echo "$name already running"
    return
  fi

  echo "starting $name -> $log_file"
  if command -v screen >/dev/null 2>&1; then
    screen -S "marketpulse-$name" -X quit >/dev/null 2>&1 || true
    screen -dmS "marketpulse-$name" bash -lc "cd '$ROOT' && $cmd >>'$log_file' 2>&1"
  else
    nohup bash -lc "cd '$ROOT' && $cmd" >>"$log_file" 2>&1 &
    disown "$!" 2>/dev/null || true
  fi
}

if should_start "collector-ashare"; then
  start_proc "collector-ashare" "python -m apps.collector.ashare.main" \
    ". .venv/bin/activate && python -m apps.collector.ashare.main"
fi

if should_start "collector-us"; then
  start_proc "collector-us" "python -m apps.collector.us.main" \
    ". .venv/bin/activate && python -m apps.collector.us.main"
fi

if should_start "collector-crypto"; then
  start_proc "collector-crypto" "python -m apps.collector.crypto.main" \
    ". .venv/bin/activate && python -m apps.collector.crypto.main"
fi

if should_start "api"; then
  start_proc "api" "uvicorn apps.api.main:app" \
    ". .venv/bin/activate && uvicorn apps.api.main:app --port 8787"
fi

if should_start "web"; then
  start_proc "web" "next dev -p 3000" \
    "cd apps/web && npm run dev"
fi

echo "waiting for health checks..."
sleep "${MARKETPULSE_START_WAIT_S:-8}"
"$ROOT/scripts/dev-status.sh" "${selected[@]}"
