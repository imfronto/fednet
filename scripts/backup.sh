#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

: "${BACKUP_RESTIC_REPOSITORY:?set BACKUP_RESTIC_REPOSITORY in .env}"
: "${BACKUP_RESTIC_PASSWORD:?set BACKUP_RESTIC_PASSWORD in .env}"

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
trap 'echo "==> Restarting stack"; make up' EXIT

# Pi-hole's gravity.db, Grafana/Uptime Kuma's SQLite files, and
# Prometheus's TSDB all risk a corrupt copy if read mid-write, so the
# stack stays down for the few seconds this takes.
echo "==> Backing up data/, .env, and the Grafana/Prometheus volumes"
restic backup /backup/data /backup/env /backup/volumes --tag fednet --host fednet

trap - EXIT
echo "==> Restarting stack"
make up

echo "==> Applying retention policy (daily=${BACKUP_KEEP_DAILY:-7} weekly=${BACKUP_KEEP_WEEKLY:-4} monthly=${BACKUP_KEEP_MONTHLY:-6})"
restic forget \
  --keep-daily "${BACKUP_KEEP_DAILY:-7}" \
  --keep-weekly "${BACKUP_KEEP_WEEKLY:-4}" \
  --keep-monthly "${BACKUP_KEEP_MONTHLY:-6}" \
  --prune

echo "==> Done"
restic snapshots --latest 1 --compact
