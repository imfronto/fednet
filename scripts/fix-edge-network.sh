#!/bin/bash
# Recreates the 'edge' Docker network when its firewall rules are missing.
#
# Root cause: 'edge' is declared `external: true` in docker-compose.yml, so
# `docker compose down`/`up` never deletes or recreates it - only the
# containers reattach. Docker only (re)installs a bridge network's
# DOCKER-BRIDGE forwarding rule when the network itself is created, not when
# containers merely reattach to one that already exists. After a host
# reboot, restart:unless-stopped containers reattach to the still-existing
# 'edge' network before this rule is restored, so every packet between
# them hits the host's default-DROP FORWARD policy and every service
# behind Caddy (Homepage included) becomes unreachable from every other
# container, even though each container itself looks healthy.
#
# Fix: detect the missing rule and, only then, tear down, recreate the
# network identically, and bring the stack back up - which forces Docker
# to reinstall it. Safe to run any time: it's a no-op if the rule is
# already present.
set -euo pipefail
cd "$(dirname "$0")/.."

NETWORK=edge
SUBNET=172.19.0.0/16
GATEWAY=172.19.0.1

NET_ID=$(docker network inspect "$NETWORK" --format '{{.Id}}' 2>/dev/null | cut -c1-12) || NET_ID=""

if [ -n "$NET_ID" ] && iptables -S DOCKER-BRIDGE 2>/dev/null | grep -q -- "-o br-${NET_ID} "; then
    echo "fix-edge-network: '${NETWORK}' firewall rule present (br-${NET_ID}) - nothing to do"
    exit 0
fi

echo "fix-edge-network: '${NETWORK}' firewall rule missing (or network absent) - recreating"
make down
docker network rm "$NETWORK" 2>/dev/null || true
docker network create --driver bridge --subnet "$SUBNET" --gateway "$GATEWAY" "$NETWORK"
make up
echo "fix-edge-network: done"
