#!/bin/sh
set -eu

# curl/jq baked into fednet/watchdog-base:1 at build time, not installed here.

PIHOLE_PASSWORD="${PIHOLE_PASSWORD:?PIHOLE_PASSWORD must be set}"
PIHOLE_STATS_WEBHOOK_URL="${PIHOLE_STATS_WEBHOOK_URL:-}"
GRAVITY_WEBHOOK_URL="${GRAVITY_WEBHOOK_URL:-}"
WEBHOOK_URL="${PIHOLE_STATS_WEBHOOK_URL:-$GRAVITY_WEBHOOK_URL}"
TOP_COUNT="${PIHOLE_STATS_TOP_COUNT:-5}"
INTERVAL_SECONDS="${PIHOLE_STATS_INTERVAL_SECONDS:-3600}"
PIHOLE_URL="http://pihole"

# .env values reach here unvalidated — ${VAR:-default} only covers unset
# or empty, not "set to garbage" or "set to zero". INTERVAL_SECONDS feeds
# `now % INTERVAL_SECONDS` below: a non-numeric value is a fatal
# arithmetic-expansion error under `set -e` (crash-loops the container
# on every restart), and zero specifically is a division-by-zero on the
# same line even though it passes a plain digits-only check.
case "$TOP_COUNT" in ''|*[!0-9]*) echo "invalid PIHOLE_STATS_TOP_COUNT '$TOP_COUNT', using default 5" >&2; TOP_COUNT=5 ;; esac
case "$INTERVAL_SECONDS" in ''|*[!0-9]*|0) echo "invalid PIHOLE_STATS_INTERVAL_SECONDS '$INTERVAL_SECONDS', using default 3600" >&2; INTERVAL_SECONDS=3600 ;; esac

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

  iso_now=$(date -u -d "@$now" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || iso_now=""

  # Discord embed: fields render as a clean label/value grid instead of
  # a wall of plain text. EMBED_COLOR is fednet's Homepage accent
  # (sky-400, #38BDF8) so this matches the Grafana/Uptime Kuma theming.
  payload=$(jq -n \
    --argjson hist "$hist" --argjson dom "$dom" --argjson cli "$cli" --argjson qt "$qt" \
    --argjson from "$from" --arg iso "$iso_now" \
    '
    ($hist.history // [] | map(select(.timestamp >= $from))) as $buckets |
    ($buckets | map(.total) | add // 0) as $total |
    ($buckets | map(.blocked) | add // 0) as $blocked |
    ($buckets | map(.cached) | add // 0) as $cached |
    ($buckets | map(.forwarded) | add // 0) as $forwarded |
    (if $total > 0 then (($blocked / $total) * 100) else 0 end) as $pct |
    ($dom.domains // [] | map("**\(.count)** — \(.domain)") | join("\n")) as $domlines |
    ($cli.clients // [] | map("**\(.count)** — \(if .name != "" then .name else .ip end)") | join("\n")) as $clilines |
    ($qt.types // {} | to_entries | map(select(.value > 0)) | sort_by(-.value) | map("\(.key) \(.value)") | join("  ·  ")) as $qtline |
    {
      embeds: [
        ({
          title: "📊 Pi-hole — Last Hour",
          color: 3718648,
          fields: [
            {name: "Total Queries", value: ($total | tostring), inline: true},
            {name: "Blocked", value: "\($blocked) (\(($pct * 10 | round) / 10)%)", inline: true},
            {name: "Cached / Forwarded", value: "\($cached) / \($forwarded)", inline: true},
            {name: "Top Blocked Domains", value: (if $domlines == "" then "_none_" else $domlines end), inline: false},
            {name: "Top Clients", value: (if $clilines == "" then "_none_" else $clilines end), inline: false},
            {name: "Query Types", value: (if $qtline == "" then "_none_" else $qtline end), inline: false}
          ],
          footer: {text: "fednet pihole-stats-digest"}
        } + (if $iso != "" then {timestamp: $iso} else {} end))
      ]
    }
    ')

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
