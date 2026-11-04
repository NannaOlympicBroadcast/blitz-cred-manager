#!/usr/bin/env sh
# Blitz Credential Manager — Linux / macOS 保活脚本
URL="${KEEPALIVE_URL:-https://cred-manager.blitz48.blitz.cloud/api/health}"
INTERVAL="${KEEPALIVE_INTERVAL:-600}"

echo "[KeepAlive] Pinging $URL every ${INTERVAL}s ..."
while true; do
  TS="$(date '+%Y-%m-%d %H:%M:%S')"
  if OUT="$(curl -fsSL -m 30 -H 'User-Agent: Blitz-KeepAlive-Sh/1.0' "$URL" 2>&1)"; then
    echo "[$TS] OK -> $OUT"
  else
    echo "[$TS] ERR -> $OUT" >&2
  fi
  sleep "$INTERVAL"
done
