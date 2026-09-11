#!/bin/sh
set -eu

# curl/jq baked into fednet/watchdog-base:1 at build time, not installed here.

SYSTEM_WEBHOOK_URL="${SYSTEM_WEBHOOK_URL:-}"
GRAVITY_WEBHOOK_URL="${GRAVITY_WEBHOOK_URL:-}"
WEBHOOK_URL="${SYSTEM_WEBHOOK_URL:-$GRAVITY_WEBHOOK_URL}"
CPU_THRESHOLD="${SYSTEM_CPU_THRESHOLD:-45}"
DISK_THRESHOLD="${SYSTEM_DISK_THRESHOLD:-90}"
TEMP_THRESHOLD="${SYSTEM_TEMP_THRESHOLD:-80}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-60}"
COOLDOWN_SECONDS="${SYSTEM_WATCHDOG_COOLDOWN_SECONDS:-1800}"
PROM_URL="http://prometheus:9090"

# .env values reach here unvalidated — ${VAR:-default} only covers unset
# or empty, not "set to garbage". A non-numeric threshold would make the
# `-ge` comparisons below silently and permanently false (no error, no
# crash — that check just never fires again); a non-numeric
# CHECK_INTERVAL_SECONDS would crash-loop the container via `sleep`.
case "$CPU_THRESHOLD" in ''|*[!0-9]*) echo "invalid SYSTEM_CPU_THRESHOLD '$CPU_THRESHOLD', using default 45" >&2; CPU_THRESHOLD=45 ;; esac
case "$DISK_THRESHOLD" in ''|*[!0-9]*) echo "invalid SYSTEM_DISK_THRESHOLD '$DISK_THRESHOLD', using default 90" >&2; DISK_THRESHOLD=90 ;; esac
case "$TEMP_THRESHOLD" in ''|*[!0-9]*) echo "invalid SYSTEM_TEMP_THRESHOLD '$TEMP_THRESHOLD', using default 80" >&2; TEMP_THRESHOLD=80 ;; esac
case "$CHECK_INTERVAL_SECONDS" in ''|*[!0-9]*|0) echo "invalid CHECK_INTERVAL_SECONDS '$CHECK_INTERVAL_SECONDS', using default 60" >&2; CHECK_INTERVAL_SECONDS=60 ;; esac
case "$COOLDOWN_SECONDS" in ''|*[!0-9]*) echo "invalid SYSTEM_WATCHDOG_COOLDOWN_SECONDS '$COOLDOWN_SECONDS', using default 1800" >&2; COOLDOWN_SECONDS=1800 ;; esac

send_alert() {
  msg="$1"
  payload=$(jq -n --arg m "$msg" '{text: $m, content: $m}')
  if curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "$payload" "$WEBHOOK_URL" >/dev/null 2>&1; then
    echo "alerted: $msg"
  else
    echo "alert POST failed: $msg" >&2
  fi
}

# Same thresholds as the red steps on the Server Overview Grafana
# dashboard (grafana/provisioning/dashboards/server-overview.json) by
# default, so "the panel is red" and "you get paged" are the same event
# unless you deliberately tune one but not the other.
last_cpu_alert=0
last_disk_alert=0
last_temp_alert=0

echo "watching $PROM_URL every ${CHECK_INTERVAL_SECONDS}s (cpu >= ${CPU_THRESHOLD}%, disk >= ${DISK_THRESHOLD}%, temp >= ${TEMP_THRESHOLD}C)"

while true; do
  now=$(date +%s)

  cpu=$(curl -fsS -G "$PROM_URL/api/v1/query" \
    --data-urlencode 'query=100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)' 2>/dev/null \
    | jq -r '.data.result[0].value[1] // "" | if . == "" then "" else (tonumber | round | tostring) end' \
    2>/dev/null) || cpu=""

  if [ -n "$cpu" ] && [ "$cpu" -ge "$CPU_THRESHOLD" ] 2>/dev/null; then
    if [ $((now - last_cpu_alert)) -ge "$COOLDOWN_SECONDS" ]; then
      send_alert "fednet: high CPU usage - ${cpu}% (threshold ${CPU_THRESHOLD}%)"
      last_cpu_alert=$now
    fi
  fi

  # Root filesystem only. /boot is a real, separate 2G partition
  # (/dev/sda2) that does matter, but node-exporter can't see it
  # accurately on this host — see the node-exporter volumes comment in
  # services/monitoring.yml for why (a snap-dockerd bind-mount quirk, the
  # same one already documented on the cadvisor block above it).
  disk=$(curl -fsS -G "$PROM_URL/api/v1/query" \
    --data-urlencode 'query=((node_filesystem_size_bytes{mountpoint="/"} - node_filesystem_free_bytes{mountpoint="/"}) / (node_filesystem_size_bytes{mountpoint="/"} - node_filesystem_free_bytes{mountpoint="/"} + node_filesystem_avail_bytes{mountpoint="/"})) * 100' 2>/dev/null \
    | jq -r '.data.result[0].value[1] // "" | if . == "" then "" else (tonumber | round | tostring) end' \
    2>/dev/null) || disk=""

  if [ -n "$disk" ] && [ "$disk" -ge "$DISK_THRESHOLD" ] 2>/dev/null; then
    if [ $((now - last_disk_alert)) -ge "$COOLDOWN_SECONDS" ]; then
      send_alert "fednet: high disk usage on / - ${disk}% (threshold ${DISK_THRESHOLD}%)"
      last_disk_alert=$now
    fi
  fi

  # max() across both CPU package thermal zones — either one running hot
  # is worth alerting on, not just the average of the two.
  temp=$(curl -fsS -G "$PROM_URL/api/v1/query" \
    --data-urlencode 'query=max(node_thermal_zone_temp)' 2>/dev/null \
    | jq -r '.data.result[0].value[1] // "" | if . == "" then "" else (tonumber | round | tostring) end' \
    2>/dev/null) || temp=""

  if [ -n "$temp" ] && [ "$temp" -ge "$TEMP_THRESHOLD" ] 2>/dev/null; then
    if [ $((now - last_temp_alert)) -ge "$COOLDOWN_SECONDS" ]; then
      send_alert "fednet: high CPU temperature - ${temp}C (threshold ${TEMP_THRESHOLD}C)"
      last_temp_alert=$now
    fi
  fi

  sleep "$CHECK_INTERVAL_SECONDS"
done
