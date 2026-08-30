#!/bin/sh
set -eu

apk add --no-cache curl jq coreutils >/dev/null 2>&1 || true

HONEYPOT_WEBHOOK_URL="${HONEYPOT_WEBHOOK_URL:-}"
GRAVITY_WEBHOOK_URL="${GRAVITY_WEBHOOK_URL:-}"
WEBHOOK_URL="${HONEYPOT_WEBHOOK_URL:-$GRAVITY_WEBHOOK_URL}"
LOG_FILE="/data/honeypot/opencanary.log"

send_alert() {
  # $logdata below is arbitrary JSON (may contain quotes, e.g. captured
  # credentials), so the payload is built with jq rather than raw string
  # interpolation to keep it valid regardless of what's in the message.
  msg="$1"
  payload=$(jq -n --arg m "$msg" '{text: $m, content: $m}')
  if curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "$payload" "$WEBHOOK_URL" >/dev/null 2>&1; then
    echo "alerted: $msg"
  else
    echo "alert POST failed: $msg" >&2
  fi
}

proto_name() {
  case "$1" in
    21) echo "FTP" ;;
    23) echo "Telnet" ;;
    3306) echo "MySQL" ;;
    6379) echo "Redis" ;;
    27017) echo "MongoDB" ;;
    *) echo "port $1" ;;
  esac
}

while [ ! -f "$LOG_FILE" ]; do
  sleep 2
done

echo "watching $LOG_FILE, alerting to $([ -n "$HONEYPOT_WEBHOOK_URL" ] && echo HONEYPOT_WEBHOOK_URL || echo GRAVITY_WEBHOOK_URL)"

tail -n0 -F "$LOG_FILE" | while IFS= read -r line; do
  src_host=$(echo "$line" | jq -r '.src_host // "unknown"' 2>/dev/null) || continue
  dst_port=$(echo "$line" | jq -r '.dst_port // "?"' 2>/dev/null) || continue
  logdata=$(echo "$line" | jq -c '.logdata // {}' 2>/dev/null) || continue
  proto=$(proto_name "$dst_port")

  msg="fednet honeypot: $proto probe from $src_host - $logdata"
  send_alert "$msg"
done
