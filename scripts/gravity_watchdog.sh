#!/bin/sh
set -eu

# curl baked into fednet/watchdog-base:1 at build time, not installed here.

WEBHOOK_URL="${WEBHOOK_URL:?WEBHOOK_URL must be set}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-48}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-3600}"
GRAVITY_DB="/etc/pihole/gravity.db"

# .env values reach here unvalidated — ${VAR:-default} only covers unset
# or empty, not "set to garbage". A non-numeric CHECK_INTERVAL_SECONDS
# would crash-loop the container via `sleep`; a non-numeric MAX_AGE_HOURS
# makes the `-gt` comparison below error out to stderr every loop
# without crashing, but staleness detection effectively stops working.
case "$MAX_AGE_HOURS" in ''|*[!0-9]*) echo "invalid MAX_AGE_HOURS '$MAX_AGE_HOURS', using default 48" >&2; MAX_AGE_HOURS=48 ;; esac
case "$CHECK_INTERVAL_SECONDS" in ''|*[!0-9]*|0) echo "invalid CHECK_INTERVAL_SECONDS '$CHECK_INTERVAL_SECONDS', using default 3600" >&2; CHECK_INTERVAL_SECONDS=3600 ;; esac

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
