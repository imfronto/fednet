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

mkdir -p data/caddy/data data/caddy/config data/portainer data/uptime-kuma

echo "Setup done. Copy .env.example to .env, then run 'make up'."
