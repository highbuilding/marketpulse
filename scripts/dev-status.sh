#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
selected=("${@:-all}")

should_check() {
  local name="$1"
  for item in "${selected[@]}"; do
    if [[ "$item" == "all" || "$item" == "$name" ]]; then
      return 0
    fi
  done
  return 1
}

check_proc() {
  local name="$1"
  local pattern="$2"
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    echo "ok   $name"
  else
    echo "down $name"
  fi
}

check_http() {
  local name="$1"
  local url="$2"
  if curl -fsS -m 3 "$url" >/dev/null 2>&1; then
    echo "ok   $name $url"
  else
    echo "down $name $url"
  fi
}

check_http_with_proc() {
  local name="$1"
  local url="$2"
  local pattern="$3"
  if curl -fsS -m 3 "$url" >/dev/null 2>&1; then
    echo "ok   $name $url"
  elif pgrep -f "$pattern" >/dev/null 2>&1; then
    echo "wait $name $url (process running, HTTP not ready yet)"
  else
    echo "down $name $url"
  fi
}

cd "$ROOT"

if docker exec marketpulse-redis-dev redis-cli ping >/dev/null 2>&1; then
  echo "ok   redis"
else
  echo "down redis"
fi

if should_check "collector-ashare"; then
  check_proc "collector-ashare" "python -m apps.collector.ashare.main"
fi
if should_check "collector-us"; then
  check_proc "collector-us" "python -m apps.collector.us.main"
fi
if should_check "collector-crypto"; then
  check_proc "collector-crypto" "python -m apps.collector.crypto.main"
fi
if should_check "api"; then
  check_proc "api" "uvicorn apps.api.main:app"
fi
if should_check "web"; then
  check_proc "web" "next dev -p 3000"
fi

if should_check "api"; then
  check_http_with_proc "api" "http://127.0.0.1:8787/api/health" "uvicorn apps.api.main:app"
fi
if should_check "collector-ashare"; then
  check_http_with_proc "collector-ashare" "http://127.0.0.1:8788/health" "python -m apps.collector.ashare.main"
fi
if should_check "collector-us"; then
  check_http_with_proc "collector-us" "http://127.0.0.1:8789/health" "python -m apps.collector.us.main"
fi
if should_check "collector-crypto"; then
  check_http_with_proc "collector-crypto" "http://127.0.0.1:8790/health" "python -m apps.collector.crypto.main"
fi
if should_check "web"; then
  check_http_with_proc "web" "http://127.0.0.1:3000" "next dev -p 3000"
fi

echo "logs: /tmp/marketpulse/*.log and data/logs/*.log"
