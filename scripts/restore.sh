#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

: "${BACKUP_RESTIC_REPOSITORY:?set BACKUP_RESTIC_REPOSITORY in .env}"
: "${BACKUP_RESTIC_PASSWORD:?set BACKUP_RESTIC_PASSWORD in .env}"

RESTIC_IMAGE="restic/restic:latest"
SNAPSHOT="${1:-latest}"

CONTAINER_REPO="$BACKUP_RESTIC_REPOSITORY"
REPO_MOUNT=()
if [[ "$BACKUP_RESTIC_REPOSITORY" == /* ]]; then
  CONTAINER_REPO="/repo"
  # restic writes a lock file even for a read-only restore, so this can't
  # be mounted :ro despite the operation not touching snapshot data.
  REPO_MOUNT=(-v "$BACKUP_RESTIC_REPOSITORY:/repo")
fi

restic() {
  docker run --rm \
    -e RESTIC_REPOSITORY="$CONTAINER_REPO" \
    -e RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
    "${REPO_MOUNT[@]}" \
    -v "$(pwd)/data:/backup/data" \
    -v "$(pwd)/.env:/backup/env" \
    -v pihole_grafana_data:/backup/volumes/grafana \
    -v pihole_prometheus_data:/backup/volumes/prometheus \
    "$RESTIC_IMAGE" "$@"
}

echo "This will stop the stack and overwrite ./data, .env, and the"
echo "Grafana/Prometheus volumes with snapshot '$SNAPSHOT'."
read -rp "Continue? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1

make down

restic restore "$SNAPSHOT" --target /

make up

echo "==> Restore complete. Check 'make ps' and each service's data."
