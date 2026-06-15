#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

check_proc "collector-ashare" "python -m apps.collector.ashare.main"
check_proc "collector-us" "python -m apps.collector.us.main"
check_proc "collector-crypto" "python -m apps.collector.crypto.main"
check_proc "api" "uvicorn apps.api.main:app"
check_proc "web" "next dev -p 3000"

check_http_with_proc "api" "http://127.0.0.1:8787/api/health" "uvicorn apps.api.main:app"
check_http_with_proc "collector-ashare" "http://127.0.0.1:8788/health" "python -m apps.collector.ashare.main"
check_http_with_proc "collector-us" "http://127.0.0.1:8789/health" "python -m apps.collector.us.main"
check_http_with_proc "collector-crypto" "http://127.0.0.1:8790/health" "python -m apps.collector.crypto.main"
check_http_with_proc "web" "http://127.0.0.1:3000" "next dev -p 3000"

echo "logs: /tmp/marketpulse/*.log and data/logs/*.log"
