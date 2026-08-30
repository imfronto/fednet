#!/bin/sh
set -eu

apk add --no-cache curl jq >/dev/null 2>&1 || true

PIHOLE_PASSWORD="${PIHOLE_PASSWORD:?PIHOLE_PASSWORD must be set}"
PIHOLE_STATS_WEBHOOK_URL="${PIHOLE_STATS_WEBHOOK_URL:-}"
GRAVITY_WEBHOOK_URL="${GRAVITY_WEBHOOK_URL:-}"
WEBHOOK_URL="${PIHOLE_STATS_WEBHOOK_URL:-$GRAVITY_WEBHOOK_URL}"
TOP_COUNT="${PIHOLE_STATS_TOP_COUNT:-5}"
INTERVAL_SECONDS="${PIHOLE_STATS_INTERVAL_SECONDS:-3600}"
PIHOLE_URL="http://pihole"

send_digest() {
  now=$(date +%s)
  from=$((now - INTERVAL_SECONDS))

  # Pi-hole v6's session-based API: log in, use the sid for the reads,
  # then log out so we don't accumulate a dangling session every hour.
  sid=$(curl -fsS -X POST "$PIHOLE_URL/api/auth" \
    --data "$(jq -n --arg p "$PIHOLE_PASSWORD" '{password: $p}')" 2>/dev/null \
    | jq -r '.session.sid // empty') || sid=""

  if [ -z "$sid" ]; then
    echo "pihole auth failed, skipping this digest" >&2
    return
  fi

  # /api/history is 10-minute buckets we sum ourselves for an accurate
  # last-hour total (the plain /api/stats/summary endpoint is a
  # cumulative "since midnight" figure, not a sliding window).
  hist=$(curl -fsS "$PIHOLE_URL/api/history" -H "sid: $sid" 2>/dev/null) || hist='{"history":[]}'

  # The "database" stats endpoints (unlike the in-memory ones) actually
  # honor from/until, so these two are true last-hour top-N lists.
  dom=$(curl -fsS -G "$PIHOLE_URL/api/stats/database/top_domains" \
    --data-urlencode "blocked=true" --data-urlencode "count=$TOP_COUNT" \
    --data-urlencode "from=$from" --data-urlencode "until=$now" \
    -H "sid: $sid" 2>/dev/null) || dom='{"domains":[]}'
  cli=$(curl -fsS -G "$PIHOLE_URL/api/stats/database/top_clients" \
    --data-urlencode "count=$TOP_COUNT" \
    --data-urlencode "from=$from" --data-urlencode "until=$now" \
    -H "sid: $sid" 2>/dev/null) || cli='{"clients":[]}'
  qt=$(curl -fsS -G "$PIHOLE_URL/api/stats/database/query_types" \
    --data-urlencode "from=$from" --data-urlencode "until=$now" \
    -H "sid: $sid" 2>/dev/null) || qt='{"types":{}}'

  curl -fsS -X DELETE "$PIHOLE_URL/api/auth" -H "sid: $sid" >/dev/null 2>&1 || true

  msg=$(jq -n \
    --argjson hist "$hist" --argjson dom "$dom" --argjson cli "$cli" --argjson qt "$qt" \
    --argjson from "$from" \
    '
    ($hist.history // [] | map(select(.timestamp >= $from))) as $buckets |
    ($buckets | map(.total) | add // 0) as $total |
    ($buckets | map(.blocked) | add // 0) as $blocked |
    ($buckets | map(.cached) | add // 0) as $cached |
    ($buckets | map(.forwarded) | add // 0) as $forwarded |
    (if $total > 0 then (($blocked / $total) * 100) else 0 end) as $pct |
    ($dom.domains // [] | map("\(.count)x \(.domain)") | join("\n")) as $domlines |
    ($cli.clients // [] | map("\(.count)x \(if .name != "" then .name else .ip end)") | join("\n")) as $clilines |
    ($qt.types // {} | to_entries | map(select(.value > 0)) | sort_by(-.value) | map("\(.key) \(.value)") | join(", ")) as $qtline |
    "**fednet pihole — last hour**\n" +
    "Queries: \($total) total, \($blocked) blocked (\(($pct * 10 | round) / 10)%)\n" +
    "Cached: \($cached) · Forwarded: \($forwarded)\n\n" +
    "Top blocked domains:\n" + (if $domlines == "" then "(none)" else $domlines end) + "\n\n" +
    "Top clients:\n" + (if $clilines == "" then "(none)" else $clilines end) + "\n\n" +
    "Query types: " + (if $qtline == "" then "(none)" else $qtline end)
    ')

  payload=$(jq -n --arg m "$msg" '{content: $m, text: $m}')
  if curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "$payload" "$WEBHOOK_URL" >/dev/null 2>&1; then
    echo "digest sent"
  else
    echo "digest POST failed" >&2
  fi
}

# Align the first send to the top of the hour (well, top of whatever
# INTERVAL_SECONDS divides evenly into), then repeat every interval.
now=$(date +%s)
sleep_secs=$((INTERVAL_SECONDS - (now % INTERVAL_SECONDS)))
echo "pihole stats digest: first send in ${sleep_secs}s, then every ${INTERVAL_SECONDS}s"
sleep "$sleep_secs"

while true; do
  send_digest
  sleep "$INTERVAL_SECONDS"
done
