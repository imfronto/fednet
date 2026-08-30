# homelab

Docker Compose-based self-hosted services stack for `fednet`, reachable over
Tailscale so nothing needs to be exposed to the public internet.

## Layout

- `docker-compose.yml` — the core stack: Caddy (front door), Homepage
  (dashboard), Portainer (container management). Always on.
- `services/*.yml` — one compose file per additional service. The Makefile
  picks up every file in this directory automatically.
- `homepage/` — Homepage dashboard config (edit `services.yaml` to add links).
- `data/` — persistent volumes for every container (git-ignored).
- `scripts/setup.sh` — one-time host setup (Tailscale install, docker
  network, data directories).

## First run

```bash
./scripts/setup.sh          # installs Tailscale, creates the `edge` network
sudo tailscale up           # join your tailnet (interactive login)
cp .env.example .env        # adjust TZ/PUID/PGID if needed
make up
```

- Dashboard: `http://<tailscale-magicdns-name>/`
- Portainer: `http://<tailscale-magicdns-name>:9000`
- Uptime Kuma: `http://<tailscale-magicdns-name>:3001`
- Pi-hole: `http://<tailscale-magicdns-name>:8081/admin` (password in `.env` as `PIHOLE_PASSWORD`)

To actually get ad-blocking, point your router's DHCP DNS setting (or each
device individually) at this server's LAN IP — Pi-hole only filters queries
that are sent to it.

## Adding a new service

1. Create `services/<name>.yml` with its own `services:` block, attach it to
   the external `edge` network, and give it persistent volumes under
   `./data/<name>`.
2. Add a link for it in `homepage/services.yaml`.
3. `make up` — the Makefile globs `services/*.yml` automatically, no other
   file needs to change.

## Commands

| Command      | Effect                                   |
|--------------|-------------------------------------------|
| `make up`    | Start/update everything                   |
| `make down`  | Stop everything                           |
| `make restart` | Recreate everything                    |
| `make ps`    | Show container status                     |
| `make logs`  | Tail logs for all containers              |
| `make pull`  | Pull latest images                        |
