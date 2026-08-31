#!/usr/bin/env bash
set -euo pipefail

# One-time host setup: shared docker network, data dirs, Tailscale install.

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Installing Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh
  echo "Run 'sudo tailscale up' to join your tailnet, then this server"
  echo "will be reachable at http://<magicdns-name> from any tailnet device."
fi

docker network inspect edge >/dev/null 2>&1 || docker network create edge

mkdir -p data/caddy/data data/caddy/config data/portainer data/uptime-kuma \
  data/pihole/etc-pihole data/pihole/etc-dnsmasq.d data/honeypot data/loki \
  data/ollama data/openwebui

# Loki's image runs as uid 10001 (not root), so its bind-mounted data dir
# needs to be owned by that uid or it fails on startup with "permission
# denied" trying to create its own subdirectories. A plain `chown` can't
# assign a uid the host user doesn't own without root, so this uses a
# throwaway container (which runs as root inside its own namespace) to
# do it instead — same trick as everywhere else in this repo that needs
# root for a bind-mounted volume without requiring host sudo.
docker run --rm -v "$(pwd)/data/loki:/data" alpine chown -R 10001:10001 /data

echo "Setup done. Copy .env.example to .env, then run 'make up'."
echo "See README 'Backups' before relying on this — restic needs"
echo "BACKUP_RESTIC_REPOSITORY and BACKUP_RESTIC_PASSWORD set in .env."
