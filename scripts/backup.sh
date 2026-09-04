#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

: "${BACKUP_RESTIC_REPOSITORY:?set BACKUP_RESTIC_REPOSITORY in .env}"
: "${BACKUP_RESTIC_PASSWORD:?set BACKUP_RESTIC_PASSWORD in .env}"

# Reuses gravity-watchdog's webhook by default so one Discord/Slack
# incoming webhook covers both alerts; set BACKUP_WEBHOOK_URL in .env to
# point backup failures somewhere else instead.
BACKUP_WEBHOOK_URL="${BACKUP_WEBHOOK_URL:-${GRAVITY_WEBHOOK_URL:-changeme}}"

send_alert() {
  [ -n "$BACKUP_WEBHOOK_URL" ] && [ "$BACKUP_WEBHOOK_URL" != "changeme" ] || return 0
  msg="$1"
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "{\"text\":\"$msg\",\"content\":\"$msg\"}" "$BACKUP_WEBHOOK_URL" >/dev/null 2>&1 || true
}

STACK_DOWN=0
cleanup() {
  status=$?
  if [ "$STACK_DOWN" -eq 1 ]; then
    echo "==> Restarting stack"
    make up
  fi
  if [ "$status" -ne 0 ]; then
    send_alert "fednet backup FAILED (exit $status) on $(hostname) at $(date -Iseconds) — check /home/fronto/backups/fednet-backup.log"
  fi
  exit "$status"
}
trap cleanup EXIT

# restic runs inside a container (as root) rather than on the host, for
# two reasons: several services (Caddy, Portainer) don't drop privileges,
# so their data under ./data is root-owned and unreadable by a host-user
# restic; and mounting everything at fixed container-internal paths keeps
# snapshot contents independent of this host's absolute paths, so restore
# isn't tied to restoring onto this exact machine at this exact location.
RESTIC_IMAGE="restic/restic:latest"

# A local filesystem path has to be bind-mounted in; any other repository
# (sftp:, s3:, b2:, rest:, swift:, rclone:) is reached over the network
# and passed through unchanged.
CONTAINER_REPO="$BACKUP_RESTIC_REPOSITORY"
REPO_MOUNT=()
if [[ "$BACKUP_RESTIC_REPOSITORY" == /* ]]; then
  mkdir -p "$BACKUP_RESTIC_REPOSITORY"
  CONTAINER_REPO="/repo"
  REPO_MOUNT=(-v "$BACKUP_RESTIC_REPOSITORY:/repo")
fi

restic() {
  docker run --rm \
    -e RESTIC_REPOSITORY="$CONTAINER_REPO" \
    -e RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
    "${REPO_MOUNT[@]}" \
    -v "$(pwd)/data:/backup/data" \
    -v "$(pwd)/.env:/backup/env:ro" \
    -v pihole_grafana_data:/backup/volumes/grafana:ro \
    -v pihole_prometheus_data:/backup/volumes/prometheus:ro \
    "$RESTIC_IMAGE" "$@"
}

restic snapshots >/dev/null 2>&1 || {
  echo "==> No existing repo, initializing"
  restic init
}

echo "==> Stopping stack for a consistent snapshot"
make down
STACK_DOWN=1

# Pi-hole's gravity.db, Grafana/Uptime Kuma's SQLite files, and
# Prometheus's TSDB all risk a corrupt copy if read mid-write, so the
# stack stays down for the few seconds this takes.
echo "==> Backing up data/, .env, and the Grafana/Prometheus volumes"
restic backup /backup/data /backup/env /backup/volumes --tag fednet --host fednet

echo "==> Restarting stack"
make up
STACK_DOWN=0

echo "==> Applying retention policy (daily=${BACKUP_KEEP_DAILY:-7} weekly=${BACKUP_KEEP_WEEKLY:-4} monthly=${BACKUP_KEEP_MONTHLY:-6})"
restic forget \
  --keep-daily "${BACKUP_KEEP_DAILY:-7}" \
  --keep-weekly "${BACKUP_KEEP_WEEKLY:-4}" \
  --keep-monthly "${BACKUP_KEEP_MONTHLY:-6}" \
  --prune

# Offsite replication (optional). Runs after the stack is already back up,
# so a slow or failing upload never extends downtime. Reuses the same
# repo password for both repositories — restic copy needs source and
# destination credentials at once, and there's no reason for them to
# differ here. Set BACKUP_B2_RESTIC_REPOSITORY to enable; left unset, this
# is skipped entirely and the local backup above is unaffected either way.
if [ -n "${BACKUP_B2_RESTIC_REPOSITORY:-}" ]; then
  : "${B2_ACCOUNT_ID:?set B2_ACCOUNT_ID in .env}"
  : "${B2_ACCOUNT_KEY:?set B2_ACCOUNT_KEY in .env}"

  # RESTIC_FROM_REPOSITORY/RESTIC_FROM_PASSWORD must only be set for the
  # copy subcommand below — restic's init also reads them (for its unrelated
  # --copy-chunker-params feature) and refuses to run if it sees a "from"
  # repo without that explicit flag.
  restic_b2() {
    docker run --rm \
      -e RESTIC_REPOSITORY="$BACKUP_B2_RESTIC_REPOSITORY" \
      -e RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
      -e B2_ACCOUNT_ID="$B2_ACCOUNT_ID" \
      -e B2_ACCOUNT_KEY="$B2_ACCOUNT_KEY" \
      "$RESTIC_IMAGE" "$@"
  }

  restic_b2_copy() {
    docker run --rm \
      -e RESTIC_REPOSITORY="$BACKUP_B2_RESTIC_REPOSITORY" \
      -e RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
      -e RESTIC_FROM_REPOSITORY="$CONTAINER_REPO" \
      -e RESTIC_FROM_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
      -e B2_ACCOUNT_ID="$B2_ACCOUNT_ID" \
      -e B2_ACCOUNT_KEY="$B2_ACCOUNT_KEY" \
      "${REPO_MOUNT[@]}" \
      "$RESTIC_IMAGE" copy
  }

  echo "==> Replicating snapshots to Backblaze B2"
  restic_b2 snapshots >/dev/null 2>&1 || {
    echo "==> No existing B2 repo, initializing"
    restic_b2 init
  }
  restic_b2_copy
  restic_b2 forget \
    --keep-daily "${BACKUP_KEEP_DAILY:-7}" \
    --keep-weekly "${BACKUP_KEEP_WEEKLY:-4}" \
    --keep-monthly "${BACKUP_KEEP_MONTHLY:-6}" \
    --prune
  echo "==> B2 replication done"
  B2_LINE=$(restic_b2 snapshots --latest 1 --compact)
else
  echo "==> BACKUP_B2_RESTIC_REPOSITORY not set, skipping offsite replication"
fi

echo "==> Done"
restic snapshots --latest 1 --compact
[ -n "${B2_LINE:-}" ] && { echo "==> Latest B2 snapshot:"; echo "$B2_LINE"; }
