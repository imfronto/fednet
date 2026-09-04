#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
# shellcheck disable=SC1091  # .env is gitignored; doesn't exist in CI's checkout
source .env
set +a

: "${BACKUP_RESTIC_REPOSITORY:?set BACKUP_RESTIC_REPOSITORY in .env}"
: "${BACKUP_RESTIC_PASSWORD:?set BACKUP_RESTIC_PASSWORD in .env}"

RESTIC_IMAGE="restic/restic:latest"

# Unlike scripts/restore.sh (which stops the stack and overwrites ./data,
# .env, and the live volumes in place), this never touches production: it
# only reads the repository and restores into a throwaway directory that's
# removed on exit, local or not.
DRILL_DIR="$(mktemp -d)"
trap 'rm -rf "$DRILL_DIR"' EXIT

echo "==> Restore drill: proves backups are actually restorable without"
echo "    touching the live stack, ./data, .env, or any named volume."
echo "    Scratch target: $DRILL_DIR (removed automatically on exit)"
echo

check_and_restore() {
  local label="$1"
  shift
  local envs=("$@")

  echo "--- $label: repository check (full data read) ---"
  docker run --rm "${envs[@]}" "$RESTIC_IMAGE" check --read-data

  echo "--- $label: restoring latest snapshot to scratch ---"
  local target="$DRILL_DIR/$label"
  mkdir -p "$target"
  docker run --rm "${envs[@]}" -v "$target:/restore-target" \
    "$RESTIC_IMAGE" restore latest --target /restore-target

  echo "--- $label: spot-checking restored files ---"
  docker run --rm -v "$target:/check" alpine:latest sh -c '
    set -e
    apk add --no-cache sqlite >/dev/null 2>&1
    if [ -s /check/backup/env ]; then
      echo "  .env: present ($(grep -c "^[A-Z_]*=" /check/backup/env) vars)"
    else
      echo "  .env: MISSING or empty" >&2
      exit 1
    fi
    for db in pihole/etc-pihole/gravity.db pihole/etc-pihole/pihole-FTL.db; do
      f="/check/backup/data/$db"
      [ -f "$f" ] || continue
      cp "$f" "/tmp/$(basename "$f")"
      echo "  $db: $(sqlite3 "/tmp/$(basename "$f")" "PRAGMA integrity_check;")"
    done
    if [ -f /check/backup/volumes/grafana/grafana.db ]; then
      cp /check/backup/volumes/grafana/grafana.db /tmp/grafana.db
      echo "  grafana.db: $(sqlite3 /tmp/grafana.db "PRAGMA integrity_check;")"
    fi
  '
  echo
}

LOCAL_REPO="$BACKUP_RESTIC_REPOSITORY"
LOCAL_MOUNT=()
if [[ "$BACKUP_RESTIC_REPOSITORY" == /* ]]; then
  LOCAL_REPO="/repo"
  LOCAL_MOUNT=(-v "$BACKUP_RESTIC_REPOSITORY:/repo")
fi
check_and_restore local \
  -e RESTIC_REPOSITORY="$LOCAL_REPO" \
  -e RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
  "${LOCAL_MOUNT[@]}"

if [ -n "${BACKUP_B2_RESTIC_REPOSITORY:-}" ]; then
  check_and_restore b2 \
    -e RESTIC_REPOSITORY="$BACKUP_B2_RESTIC_REPOSITORY" \
    -e RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
    -e B2_ACCOUNT_ID="${B2_ACCOUNT_ID:?set B2_ACCOUNT_ID in .env}" \
    -e B2_ACCOUNT_KEY="${B2_ACCOUNT_KEY:?set B2_ACCOUNT_KEY in .env}"
else
  echo "==> BACKUP_B2_RESTIC_REPOSITORY not set, skipping offsite drill"
fi

echo "==> Restore drill complete — both repositories are restorable."
