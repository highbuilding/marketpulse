#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

stop_screen() {
  local name="$1"
  if command -v screen >/dev/null 2>&1; then
    screen -S "marketpulse-$name" -X quit >/dev/null 2>&1 || true
  fi
}

kill_pattern() {
  local name="$1"
  local pattern="$2"
  stop_screen "$name"
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    echo "stopping $name"
    pkill -9 -f "$pattern" || true
  else
    echo "$name not running"
  fi
}

kill_pattern "web" "next dev -p 3000"
kill_pattern "api" "uvicorn apps.api.main:app"
kill_pattern "collector-ashare" "python -m apps.collector.ashare.main"
kill_pattern "collector-us" "python -m apps.collector.us.main"
kill_pattern "collector-crypto" "python -m apps.collector.crypto.main"

if [[ "${1:-}" == "--with-redis" ]]; then
  echo "stopping redis"
  docker compose -f docker-compose.dev.yml stop redis >/dev/null
fi

echo "stopped"
