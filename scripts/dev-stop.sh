#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

with_redis=0
selected=()
for arg in "$@"; do
  if [[ "$arg" == "--with-redis" ]]; then
    with_redis=1
  else
    selected+=("$arg")
  fi
done
if [[ ${#selected[@]} -eq 0 ]]; then
  selected=("all")
fi

should_stop() {
  local name="$1"
  for item in "${selected[@]}"; do
    if [[ "$item" == "all" || "$item" == "$name" ]]; then
      return 0
    fi
  done
  return 1
}

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

if should_stop "web"; then
  kill_pattern "web" "next dev -p 3000"
fi
if should_stop "api"; then
  kill_pattern "api" "uvicorn apps.api.main:app"
fi
if should_stop "collector-ashare"; then
  kill_pattern "collector-ashare" "python -m apps.collector.ashare.main"
fi
if should_stop "collector-us"; then
  kill_pattern "collector-us" "python -m apps.collector.us.main"
fi
if should_stop "collector-crypto"; then
  kill_pattern "collector-crypto" "python -m apps.collector.crypto.main"
fi

if [[ "$with_redis" == "1" ]]; then
  echo "stopping redis"
  docker compose -f docker-compose.dev.yml stop redis >/dev/null
fi

echo "stopped"
