#!/bin/sh
set -eu

apk add --no-cache curl >/dev/null 2>&1 || true

WEBHOOK_URL="${WEBHOOK_URL:?WEBHOOK_URL must be set}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-48}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-3600}"
GRAVITY_DB="/etc/pihole/gravity.db"

send_alert() {
  msg="$1"
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "{\"text\":\"$msg\",\"content\":\"$msg\"}" "$WEBHOOK_URL" || true
}

while true; do
  if [ ! -f "$GRAVITY_DB" ]; then
    send_alert "pihole: gravity.db not found at $GRAVITY_DB"
  else
    now=$(date +%s)
    mtime=$(stat -c %Y "$GRAVITY_DB")
    age_hours=$(( (now - mtime) / 3600 ))
    if [ "$age_hours" -gt "$MAX_AGE_HOURS" ]; then
      send_alert "pihole: gravity.db has not updated in ${age_hours}h (threshold ${MAX_AGE_HOURS}h)"
    fi
  fi
  sleep "$CHECK_INTERVAL_SECONDS"
done
