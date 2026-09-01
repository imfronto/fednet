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

# Burst detection: many probes fast is a materially different signal than
# one-off recon (an active scan/attack in progress), so it gets its own
# escalated alert on top of the per-probe ones below — not instead of
# them. State is a plain file of recent probe timestamps, pruned to a
# sliding window on every probe; no persistence needed since a container
# restart just means "no known recent history," which is correct.
BURST_THRESHOLD="${HONEYPOT_BURST_THRESHOLD:-10}"
BURST_WINDOW_SECONDS="${HONEYPOT_BURST_WINDOW_SECONDS:-60}"
BURST_COOLDOWN_SECONDS="${HONEYPOT_BURST_COOLDOWN_SECONDS:-300}"

# .env values reach here unvalidated — ${VAR:-default} only covers unset
# or empty, not "set to garbage". A non-numeric value would otherwise hit
# the arithmetic below and, under `set -e`, kill the whole watchdog
# (not just burst detection) rather than just this one feature.
case "$BURST_THRESHOLD" in ''|*[!0-9]*) echo "invalid HONEYPOT_BURST_THRESHOLD '$BURST_THRESHOLD', using default 10" >&2; BURST_THRESHOLD=10 ;; esac
case "$BURST_WINDOW_SECONDS" in ''|*[!0-9]*) echo "invalid HONEYPOT_BURST_WINDOW_SECONDS '$BURST_WINDOW_SECONDS', using default 60" >&2; BURST_WINDOW_SECONDS=60 ;; esac
case "$BURST_COOLDOWN_SECONDS" in ''|*[!0-9]*) echo "invalid HONEYPOT_BURST_COOLDOWN_SECONDS '$BURST_COOLDOWN_SECONDS', using default 300" >&2; BURST_COOLDOWN_SECONDS=300 ;; esac
PROBE_TIMES_FILE="/tmp/probe_times"
LAST_BURST_ALERT_FILE="/tmp/last_burst_alert"
: > "$PROBE_TIMES_FILE"
echo 0 > "$LAST_BURST_ALERT_FILE"

check_burst() {
  now=$(date +%s)
  echo "$now" >> "$PROBE_TIMES_FILE"

  cutoff=$((now - BURST_WINDOW_SECONDS))
  : > "${PROBE_TIMES_FILE}.tmp"
  while IFS= read -r t; do
    [ "$t" -ge "$cutoff" ] 2>/dev/null && echo "$t" >> "${PROBE_TIMES_FILE}.tmp"
  done < "$PROBE_TIMES_FILE"
  mv "${PROBE_TIMES_FILE}.tmp" "$PROBE_TIMES_FILE"

  count=$(wc -l < "$PROBE_TIMES_FILE")
  if [ "$count" -ge "$BURST_THRESHOLD" ]; then
    last_alert=$(cat "$LAST_BURST_ALERT_FILE")
    if [ $((now - last_alert)) -ge "$BURST_COOLDOWN_SECONDS" ]; then
      send_alert "fednet honeypot: BURST - $count probes in the last ${BURST_WINDOW_SECONDS}s (possible active scan/attack, not just recon)"
      echo "$now" > "$LAST_BURST_ALERT_FILE"
    fi
  fi
}

while [ ! -f "$LOG_FILE" ]; do
  sleep 2
done

echo "watching $LOG_FILE, alerting to $([ -n "$HONEYPOT_WEBHOOK_URL" ] && echo HONEYPOT_WEBHOOK_URL || echo GRAVITY_WEBHOOK_URL)"
echo "burst detection: ${BURST_THRESHOLD}+ probes in ${BURST_WINDOW_SECONDS}s (cooldown ${BURST_COOLDOWN_SECONDS}s)"

tail -n0 -F "$LOG_FILE" | while IFS= read -r line; do
  src_host=$(echo "$line" | jq -r '.src_host // "unknown"' 2>/dev/null) || continue
  dst_port=$(echo "$line" | jq -r '.dst_port // "?"' 2>/dev/null) || continue
  logdata=$(echo "$line" | jq -c '.logdata // {}' 2>/dev/null) || continue
  proto=$(proto_name "$dst_port")

  msg="fednet honeypot: $proto probe from $src_host - $logdata"
  send_alert "$msg"
  check_burst
done
