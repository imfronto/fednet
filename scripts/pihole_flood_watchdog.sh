#!/bin/sh
set -eu

# curl/jq baked into fednet/watchdog-base:1 at build time, not installed here.

PIHOLE_FLOOD_WEBHOOK_URL="${PIHOLE_FLOOD_WEBHOOK_URL:-}"
GRAVITY_WEBHOOK_URL="${GRAVITY_WEBHOOK_URL:-}"
WEBHOOK_URL="${PIHOLE_FLOOD_WEBHOOK_URL:-$GRAVITY_WEBHOOK_URL}"
CLIENT_THRESHOLD="${PIHOLE_CLIENT_FLOOD_THRESHOLD:-600}"
TOTAL_THRESHOLD="${PIHOLE_TOTAL_FLOOD_THRESHOLD:-4000}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-60}"
COOLDOWN_SECONDS="${PIHOLE_FLOOD_COOLDOWN_SECONDS:-1800}"
PROM_URL="http://prometheus:9090"

# .env values reach here unvalidated — ${VAR:-default} only covers unset
# or empty, not "set to garbage". A non-numeric threshold would make the
# `-ge` comparisons below silently and permanently false (no error, no
# crash — flood detection just never fires again); a non-numeric
# CHECK_INTERVAL_SECONDS would crash-loop the container via `sleep`.
case "$CLIENT_THRESHOLD" in ''|*[!0-9]*) echo "invalid PIHOLE_CLIENT_FLOOD_THRESHOLD '$CLIENT_THRESHOLD', using default 600" >&2; CLIENT_THRESHOLD=600 ;; esac
case "$TOTAL_THRESHOLD" in ''|*[!0-9]*) echo "invalid PIHOLE_TOTAL_FLOOD_THRESHOLD '$TOTAL_THRESHOLD', using default 4000" >&2; TOTAL_THRESHOLD=4000 ;; esac
case "$CHECK_INTERVAL_SECONDS" in ''|*[!0-9]*|0) echo "invalid CHECK_INTERVAL_SECONDS '$CHECK_INTERVAL_SECONDS', using default 60" >&2; CHECK_INTERVAL_SECONDS=60 ;; esac
case "$COOLDOWN_SECONDS" in ''|*[!0-9]*) echo "invalid PIHOLE_FLOOD_COOLDOWN_SECONDS '$COOLDOWN_SECONDS', using default 1800" >&2; COOLDOWN_SECONDS=1800 ;; esac

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

last_client_alert=0
last_total_alert=0

echo "watching $PROM_URL every ${CHECK_INTERVAL_SECONDS}s (client threshold ${CLIENT_THRESHOLD}/min, total threshold ${TOTAL_THRESHOLD}/min)"

while true; do
  now=$(date +%s)

  # pihole-exporter already surfaces Pi-hole's own per-client 1-minute
  # query count (pihole_query_client_1m), so a single device suddenly
  # flooding DNS — a common real symptom of a compromised or infected
  # device — needs no new scraping, just reading what's already there.
  worst=$(curl -fsS -G "$PROM_URL/api/v1/query" \
    --data-urlencode "query=pihole_query_client_1m" 2>/dev/null \
    | jq -r '.data.result | if length > 0 then (max_by(.value[1] | tonumber) | "\(.metric.query_client) \(.value[1])") else "" end' \
    2>/dev/null) || worst=""

  if [ -n "$worst" ]; then
    worst_ip=${worst% *}
    worst_count=${worst#* }
    if [ "${worst_count%.*}" -ge "$CLIENT_THRESHOLD" ] 2>/dev/null; then
      if [ $((now - last_client_alert)) -ge "$COOLDOWN_SECONDS" ]; then
        send_alert "fednet pihole: possible DNS flood from $worst_ip - ${worst_count} queries in the last minute (threshold ${CLIENT_THRESHOLD})"
        last_client_alert=$now
      fi
    fi
  fi

  # Network-wide total, in case many devices are involved at once rather
  # than one obvious offender. pihole_query_count resets when Pi-hole
  # rolls its stats over each day; increase() handles that reset on its
  # own, same as any Prometheus counter.
  total=$(curl -fsS -G "$PROM_URL/api/v1/query" \
    --data-urlencode 'query=increase(pihole_query_count{category="total"}[1m])' 2>/dev/null \
    | jq -r '.data.result[0].value[1] // "" | if . == "" then "" else (tonumber | round | tostring) end' \
    2>/dev/null) || total=""

  if [ -n "$total" ]; then
    if [ "${total%.*}" -ge "$TOTAL_THRESHOLD" ] 2>/dev/null; then
      if [ $((now - last_total_alert)) -ge "$COOLDOWN_SECONDS" ]; then
        send_alert "fednet pihole: network-wide DNS query flood - ${total} queries in the last minute (threshold ${TOTAL_THRESHOLD})"
        last_total_alert=$now
      fi
    fi
  fi

  sleep "$CHECK_INTERVAL_SECONDS"
done
