<p align="center">
  <img src="docs/banner.svg" alt="fednet homelab banner" width="100%">
</p>

<p align="center">
  <img alt="stack" src="https://img.shields.io/badge/stack-docker%20compose-2496ED?logo=docker&logoColor=white">
  <img alt="dns" src="https://img.shields.io/badge/dns-pi--hole-96060C?logo=pi-hole&logoColor=white">
  <img alt="access" src="https://img.shields.io/badge/access-tailscale%20%7C%20LAN-4B4B4B?logo=tailscale&logoColor=white">
  <img alt="backups" src="https://img.shields.io/badge/backups-restic-2F5D50?logo=backblaze&logoColor=white">
  <img alt="status" src="https://img.shields.io/badge/status-personal%20project-informational">
</p>

# fednet

A Docker Compose homelab stack: a reverse-proxied dashboard with live
service widgets, network-wide ad blocking, container management,
metrics/monitoring, encrypted backups, and a 3D-printer control panel —
all self-hosted on one box, reachable either over Tailscale or a plain LAN
hostname.

Nothing here is exposed to the public internet. Every service binds to the
host's LAN interface (or a Tailscale tailnet, if you set one up); there's no
port-forwarding, no cloud dependency, and no external accounts required to
run it.

## Features

- **One dashboard, live data** — Homepage shows real-time container
  CPU/mem/status, Pi-hole's blocked-today count, Portainer's running
  container count, and Uptime Kuma's up/down summary, right on each
  service's card — no need to click through to check anything
- **DNS-level ad blocking** for the whole LAN via Pi-hole, with curated
  blocklists and a watchdog that alerts if they go stale
- **Encrypted, automated backups** — restic snapshots everything that
  isn't already in git (service data, secrets, external volumes) on a
  schedule, with retention policy and failure alerting built in
- **Zero-touch service discovery** — add a new `services/*.yml` file and
  `make up` picks it up automatically, no other file to edit
- **Reachable your way** — a local `lab.lan` hostname via Pi-hole's own
  DNS, or a Tailscale tailnet for access from anywhere, your choice (or
  both)

## Table of contents

- [Features](#features)
- [Stack overview](#stack-overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
  - [1. Clone and run one-time host setup](#1-clone-and-run-one-time-host-setup)
  - [2. Configure environment](#2-configure-environment)
  - [3. Start the stack](#3-start-the-stack)
  - [4. Give it a hostname](#4-give-it-a-hostname)
- [Service notes](#service-notes)
- [Dashboard customization](#dashboard-customization)
- [Adding a new service](#adding-a-new-service)
- [Commands](#commands)
- [Backups](#backups)
- [Honeypot](#honeypot)
- [Troubleshooting](#troubleshooting)
- [Data & persistence](#data--persistence)

## Stack overview

| Service | Purpose | Default port |
|---|---|---|
| [Caddy](https://caddyserver.com/) | Reverse proxy, front door for the dashboard | `80`, `443` |
| [Homepage](https://gethomepage.dev/) | Dashboard with shortcuts to every service | behind Caddy on `80` |
| [Portainer](https://www.portainer.io/) | Container management UI | `9000` |
| [Pi-hole](https://pi-hole.net/) | Network-wide DNS ad blocking + local DNS records | `53` (DNS), `8081` (admin) |
| [Uptime Kuma](https://github.com/louislam/uptime-kuma) | Service uptime monitoring | `3001` |
| [Grafana](https://grafana.com/) | Dashboards for metrics | `3000` |
| [Prometheus](https://prometheus.io/) | Metrics collection | `9090` |
| [BambuBuddy](https://github.com/maziggy/bambuddy) | Self-hosted Bambu Lab 3D printer dashboard | `8000` (host networking) |

Every service except BambuBuddy sits on a shared external Docker network
(`edge`) and is published directly on the host's LAN ports — Caddy only
proxies the Homepage dashboard itself on port 80. BambuBuddy runs with
`network_mode: host` instead, because printer auto-discovery (SSDP) and its
RTSP camera proxy both need raw L2 network access a bridge network can't
provide.

## Architecture

```mermaid
flowchart LR
    subgraph LAN["Your LAN"]
        Client[Any device]
    end

    Client -- "DNS query" --> Pihole[Pi-hole :53]
    Client -- "http://lab.lan" --> Caddy[Caddy :80]
    Client -- "direct ports" --> Other[Portainer / Grafana / Prometheus / Uptime Kuma / BambuBuddy]

    Pihole -- "lab.lan -> host IP" --> Caddy
    Pihole -- "blocked/allowed" --> Upstream[(Upstream DNS)]

    Caddy --> Homepage[Homepage dashboard]
    Homepage -.shortcuts.-> Other

    Prometheus[Prometheus] -- scrapes --> Pihole
    Grafana[Grafana] -- queries --> Prometheus
```

## Prerequisites

- A Linux host on your LAN with [Docker](https://docs.docker.com/engine/install/) and the Compose plugin installed
- `make`
- Root/sudo on that host (Pi-hole binds port 53, Caddy binds 80/443)
- Optional: a [Tailscale](https://tailscale.com/) account, if you'd rather reach the stack over a tailnet than a LAN hostname

## Setup

<p align="center">
  <img src="docs/install-flow.svg" alt="fednet installation flow: clone, setup, configure, launch, connect" width="100%">
</p>

### 1. Clone and run one-time host setup

```bash
git clone https://github.com/imfronto/fednet.git
cd fednet
./scripts/setup.sh
```

`scripts/setup.sh` creates the shared `edge` Docker network, the `data/`
subdirectories every service's volumes expect, and offers to install
Tailscale if it isn't already present. It's idempotent — safe to re-run.

### 2. Configure environment

```bash
cp .env.example .env
```

Then edit `.env`:

| Variable | Required | Notes |
|---|---|---|
| `TZ` | recommended | Your timezone, e.g. `America/Detroit` |
| `PUID` / `PGID` | recommended | Match your host user so bind-mounted files aren't root-owned |
| `DOCKER_GID` | recommended | GID of the host's `docker` group (`getent group docker \| cut -d: -f3`) — lets Homepage's container-stats widget read the Docker socket |
| `TAILSCALE_HOSTNAME` | if using Tailscale | Your tailnet MagicDNS name, e.g. `fednet` |
| `PIHOLE_PASSWORD` | **yes** | Pi-hole admin password — no default, container won't start without it. Also powers Homepage's Pi-hole widget |
| `GRAFANA_ADMIN_PASSWORD` | **yes** | Grafana admin password — see the [Troubleshooting](#troubleshooting) note if you're changing it on an existing volume |
| `GRAVITY_WEBHOOK_URL` | optional | Discord/Slack incoming webhook for blocklist-staleness *and* backup-failure alerts; leave as `changeme` to disable alerting |
| `GRAVITY_MAX_AGE_HOURS` | optional | Alert threshold if Pi-hole's blocklist hasn't refreshed (default `48`) |
| `BAMBUDDY_PORT` | optional | Port BambuBuddy's web UI listens on (default `8000`) |
| `PORTAINER_API_KEY` | optional | Powers Homepage's Portainer widget (running/stopped container counts). Generate under Portainer's **My Account → Access Tokens** |
| `BACKUP_RESTIC_*` | **yes**, for backups | See [Backups](#backups) |
| `HONEYPOT_WEBHOOK_URL` | optional | See [Honeypot](#honeypot) — falls back to `GRAVITY_WEBHOOK_URL` if unset |

### 3. Start the stack

```bash
make up
```

The Makefile globs every file in `services/*.yml` automatically, so this
starts the core stack (`docker-compose.yml`) plus Pi-hole, monitoring, and
BambuBuddy in one shot. First run pulls every image, so it can take a
few minutes.

Check everything came up healthy:

```bash
make ps
```

### 4. Give it a hostname

You have two options, and they aren't mutually exclusive.

**Option A — Tailscale (recommended for remote access):**

```bash
sudo tailscale up
```

Every service is then reachable at `http://<TAILSCALE_HOSTNAME>` (or
`:<port>` for anything other than the dashboard) from any device on your
tailnet, with no LAN configuration needed.

**Option B — a local hostname on your LAN (e.g. `lab.lan`):**

Since Pi-hole is already your DNS server, add a Local DNS Record instead of
relying on `/etc/hosts` on every device:

```bash
docker exec pihole sh -c '
SID=$(curl -skS -X POST "http://127.0.0.1/api/auth" --data "{\"password\":\"'"$PIHOLE_PASSWORD"'\"}" | jq -r .session.sid)
curl -skS -X PUT "http://127.0.0.1/api/config/dns/hosts/<HOST-LAN-IP>%20lab.lan" -H "sid: $SID"
'
```

(Or do the same thing from the Pi-hole admin UI: **Local DNS → DNS
Records**.) Then point your devices at Pi-hole for DNS — either set it
manually per-device, or point your router's DHCP DNS setting at the host's
LAN IP so every device on the network gets it automatically (and gets
ad-blocking for free). See [Troubleshooting](#troubleshooting) if `lab.lan`
doesn't resolve after this — there's a Pi-hole-in-Docker gotcha that bites
almost everyone who runs it in bridge mode.

## Service notes

**Pi-hole blocklists** — configured from
[blocklistproject/Lists](https://github.com/blocklistproject/Lists) plus
StevenBlack's hosts file:

- `https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts`
- `https://blocklistproject.github.io/Lists/ads.txt`
- `https://blocklistproject.github.io/Lists/tracking.txt`
- `https://blocklistproject.github.io/Lists/malware.txt`
- `https://blocklistproject.github.io/Lists/phishing.txt`
- `https://blocklistproject.github.io/Lists/ransomware.txt`
- `https://blocklistproject.github.io/Lists/scam.txt`
- `https://blocklistproject.github.io/Lists/fraud.txt`
- `https://blocklistproject.github.io/Lists/redirect.txt`

blocklistproject also publishes lists that block entire platforms outright
(facebook.txt, youtube.txt, tiktok.txt, twitter.txt, whatsapp.txt, gambling,
porn, crypto, vaping, drugs, fortnite) — deliberately not added since they
break those apps/sites entirely rather than just stripping ads. Add via the
admin UI (**Domains → Adlists**) or the API, then `docker exec pihole
pihole -g` to apply.

Pi-hole's blocklist database (`gravity.db`) lives under `data/pihole/`,
which is git-ignored — a fresh checkout starts with none configured until
you add the lists above.

`gravity-watchdog` alerts if `gravity.db` hasn't refreshed in
`GRAVITY_MAX_AGE_HOURS` (default 48h), posting to `GRAVITY_WEBHOOK_URL`.
Point it at a real Discord/Slack incoming webhook to make it useful.

**BambuBuddy** — runs on host networking and auto-discovers any Bambu Lab
printer on your LAN with Developer Mode enabled. Open `http://<host>:8000`
(or your `lab.lan`/Tailscale hostname) and add your printer from the UI —
no config file entry needed. Data (job archives, settings) persists under
`data/bambuddy/`.

**Loki** — its image runs as uid `10001` rather than root, so `data/loki`
has to be owned by that uid or it fails on startup with `permission
denied` creating its own subdirectories. `scripts/setup.sh` handles this
with a throwaway root container (same no-sudo-needed trick used
elsewhere in this repo) — if you ever recreate `data/loki` by hand,
re-run that chown: `docker run --rm -v $(pwd)/data/loki:/data alpine
chown -R 10001:10001 /data`.

**Grafana / Prometheus** — reuse the pre-existing `pihole_grafana_data` and
`pihole_prometheus_data` Docker volumes (declared `external: true` in
`services/monitoring.yml`), so dashboards and metric history survive
redeploys. `make down` never removes external volumes, but they also won't
show up if you inspect this project alone — they aren't created by
anything here.

## Dashboard customization

Homepage's config lives entirely under `homepage/` and hot-reloads most
changes without a container restart:

- `homepage/services.yaml` — shortcuts, grouped, each with an optional
  `ping:` host for a live status dot (hostname/IP only — ICMP has no
  concept of ports, so `host:port` silently fails to parse), a `container:`
  field for live Docker stats, and a `widget:` block for service-specific
  live data
- `homepage/settings.yaml` — theme, accent color, header style, card blur,
  group layout/columns
- `homepage/widgets.yaml` — the info-widget row (greeting, clock, weather,
  resource usage, search bar)
- `homepage/docker.yaml` — the Docker socket connection (`fednet:`) that
  every service's `container:` field references for live stats
- `homepage/custom.css` — the dark gradient background, grid texture, and
  card hover effects layered on top of the theme; auto-included by Homepage,
  no build step

**Live widgets** — Portainer, Uptime Kuma, and BambuBuddy's cards show live
Docker container CPU/memory/status; Pi-hole shows ads blocked today and
query totals; Portainer additionally shows running/stopped container
counts; Uptime Kuma shows an up/down/pending summary pulled from a
[status page](https://github.com/louislam/uptime-kuma) you configure inside
its own UI (**Status Pages → New Status Page**, then use the slug from its
URL). The weather widget uses [Open-Meteo](https://open-meteo.com/) — no
API key needed, just a `latitude`/`longitude` in `widgets.yaml`.

**Widget secrets** — any `key:`/password field in `services.yaml` should
reference `{{HOMEPAGE_VAR_SOMETHING}}`, never a hardcoded value: Homepage's
*only* substitution mechanism is that exact `{{HOMEPAGE_VAR_*}}` syntax
(confirmed from its source — a plain `${VAR}` is never expanded and just
gets sent as a literal string, which looks like a silent 401). The
corresponding `HOMEPAGE_VAR_SOMETHING` environment variable is set on the
`homepage` service in `docker-compose.yml`, sourced from `.env` — so the
actual secret only ever lives in `.env`, never in a file git tracks.

## Adding a new service

1. Create `services/<name>.yml` with its own `services:` block, attach it
   to the external `edge` network, and give it persistent volumes under
   `./data/<name>` (unless it needs host networking, like BambuBuddy).
2. Add a shortcut for it in `homepage/services.yaml`, and a `layout:` entry
   in `homepage/settings.yaml` if it's a new group.
3. `make up` — the Makefile globs `services/*.yml` automatically, no other
   file needs to change.

## Commands

| Command | Effect |
|---|---|
| `make up` | Start/update everything |
| `make down` | Stop everything |
| `make restart` | Recreate everything |
| `make ps` | Show container status |
| `make logs` | Tail logs for all containers |
| `make pull` | Pull latest images |
| `make backup` | Snapshot everything git doesn't track (see [Backups](#backups)) |
| `make backup-list` | List existing snapshots |
| `make restore` | Restore the latest snapshot (`make restore SNAPSHOT=<id>` for a specific one) |

## Backups

`make backup` uses [restic](https://restic.net/) to snapshot everything
`.gitignore` excludes from version control — the actual state that a `git
clone` of this repo can't reconstruct:

- `./data/` — Caddy TLS state, Portainer, Uptime Kuma, Pi-hole's
  `etc-pihole`/`etc-dnsmasq.d`, BambuBuddy's job archives
- `.env` — every service password and config value
- the two external Docker volumes (`pihole_grafana_data`,
  `pihole_prometheus_data`) that Grafana and Prometheus use, since those
  live outside `./data` entirely

restic encrypts everything client-side before it leaves the host, dedupes
across runs, and keeps a history of snapshots rather than just the latest
copy — configured entirely through `.env`:

| Variable | Notes |
|---|---|
| `BACKUP_RESTIC_REPOSITORY` | Where snapshots go — a local path, `sftp:user@host:/path`, an S3-compatible bucket, or Backblaze B2. **A local path only protects against accidental deletion or corruption, not a dead disk** — point this somewhere off the host once you have a second disk or NAS available. |
| `BACKUP_RESTIC_PASSWORD` | Encrypts and authenticates the repo. restic cannot recover a lost password and neither can anyone else — keep a copy outside this repo (password manager), or every snapshot becomes unreadable. |
| `BACKUP_KEEP_DAILY` / `BACKUP_KEEP_WEEKLY` / `BACKUP_KEEP_MONTHLY` | Retention policy applied by `restic forget --prune` after each backup. Defaults: 7 daily, 4 weekly, 6 monthly. |
| `BACKUP_WEBHOOK_URL` | Optional. Where `backup.sh` alerts on failure. Falls back to `GRAVITY_WEBHOOK_URL` if unset, so one Discord/Slack webhook can cover both gravity-watchdog and backup failures. |

`scripts/backup.sh` runs restic inside the `restic/restic` container
rather than on the host — two reasons: Caddy and Portainer don't drop
privileges, so parts of `./data` are root-owned and unreadable by a
host-user restic; and mounting everything at fixed container-internal
paths (`/backup/data`, `/backup/env`, `/backup/volumes/...`) keeps
snapshot contents independent of this host's absolute paths, so restoring
isn't tied to this exact machine or directory. It also briefly stops the
whole stack (`make down` / `make up`) around the snapshot — Pi-hole's
`gravity.db`, Grafana and Uptime Kuma's SQLite files, and Prometheus's
TSDB all risk a corrupt copy if read mid-write, so a few seconds of
downtime beats a backup that fails to restore. No host install is needed
beyond Docker itself. If anything in the backup fails, the stack is
guaranteed to come back up regardless, and `BACKUP_WEBHOOK_URL` fires so
you find out instead of discovering it the next time you need a restore.

**Restore:**

```bash
make backup-list                  # find the snapshot id you want, or use "latest"
make restore                      # restores the latest snapshot in place
make restore SNAPSHOT=a1b2c3d4    # or a specific one
```

`make restore` stops the stack, restores `./data`, `.env`, and the two
Docker volumes from the snapshot, and brings the stack back up. Because
the snapshot's paths are container-internal, this works the same way
whether you're restoring in place after a bad config change or onto a
freshly cloned repo after losing the disk — just `git clone`, run
`./scripts/setup.sh`, put `.env` back (or leave the placeholder — restore
overwrites it from the snapshot anyway), and run `make restore`.

**Automate it** with a nightly cron job:

```bash
crontab -e
# 0 3 * * * cd /home/fronto/projects/homelab && ./scripts/backup.sh >> /home/fronto/backups/fednet-backup.log 2>&1
```

## Honeypot

`services/honeypot.yml` runs [OpenCanary](https://github.com/thinkst/opencanary),
a low-interaction honeypot: it fakes five commonly-scanned services —
FTP (`21`), Telnet (`23`), MySQL (`3306`), Redis (`6379`), MongoDB
(`27017`) — and logs every connection, including any credentials
attempted. Real SSH stays on `:22`, untouched. Nothing legitimate on this
LAN ever has a reason to touch these ports, so any hit is inherently
suspicious — a compromised IoT device, a scanning bot that made it onto
the network, or a malicious guest.

It's deliberately **not** on the shared `edge` network or listed in
Homepage — it sits on its own dedicated `honeypot_net`, which has no
route to Pi-hole, Portainer, Grafana, or anything else in this stack
(verified: it can't even resolve `pihole` by name). If it were ever
actually compromised beyond OpenCanary's emulation layer, it still
couldn't pivot to anything real.

`honeypot-watchdog` tails its log and posts a formatted alert — source
IP, protocol, and whatever was captured (e.g. attempted credentials) —
to `HONEYPOT_WEBHOOK_URL`, falling back to `GRAVITY_WEBHOOK_URL` like
`backup.sh` does, so one webhook can cover all three alert sources.

**Dashboard** — Promtail ships the same log into Loki (both added to
`services/monitoring.yml`, reusing the Grafana/Prometheus already in the
stack rather than standing up a separate log viewer), and Grafana
auto-loads a provisioned **Honeypot** dashboard on startup — no manual
setup. It shows total probes, a breakdown by port, a probes-over-time
chart, and the raw log itself, filterable and searchable. Open Grafana
→ Dashboards → **Honeypot**. Neither Loki nor Promtail publish any port
to the LAN; Grafana reaches Loki over the `edge` network only.

**A note on `internal: true`** — the first version of this used
`internal: true` on `honeypot_net` for stronger isolation (no egress at
all). That turned out to also block Docker's own port-publishing, which
silently made the honeypot completely unreachable from the LAN —
defeating the entire point without any obvious error. Caught by
literally testing a connection before calling it done, not by reading
the port list and assuming it worked. Isolation here comes from *which*
network it's on, not from `internal: true`.

## Troubleshooting

**`lab.lan` (or any local DNS record) times out from LAN devices, but works
from the Pi-hole host itself** — Pi-hole defaults to `listeningMode: LOCAL`,
which only accepts queries from the container's own directly-attached
network. Since Pi-hole runs in a Docker bridge network, its only "local"
subnet from that setting's point of view is the internal Docker bridge, not
your real LAN — so it silently drops every query from actual devices. Fix:

```bash
docker exec pihole sh -c '
SID=$(curl -skS -X POST "http://127.0.0.1/api/auth" --data "{\"password\":\"'"$PIHOLE_PASSWORD"'\"}" | jq -r .session.sid)
curl -skS -X PATCH "http://127.0.0.1/api/config" -H "sid: $SID" -H "Content-Type: application/json" \
  --data "{\"config\":{\"dns\":{\"listeningMode\":\"ALL\"}}}"
'
```

**Port 53 (or 80/443) seems unreachable from other devices, even after the
above** — check whether `ufw` is active and blocking it:

```bash
sudo ufw status verbose
sudo ufw allow 53/tcp
sudo ufw allow 53/udp
```

**A device still can't resolve a hostname after the fixes above** — flush
its DNS cache; it's likely holding a cached failure from before the fix:

```bash
# macOS
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
# Windows
ipconfig /flushdns
# Linux (systemd-resolved)
resolvectl status   # confirm which DNS server the interface is actually using
resolvectl query <hostname>
```

**Changing `GRAFANA_ADMIN_PASSWORD` in `.env` doesn't change the login** —
the `GF_SECURITY_ADMIN_PASSWORD` environment variable only sets the admin
password the *first* time Grafana initializes its database. Since this
project reuses a pre-existing `pihole_grafana_data` volume, that's already
happened — updating `.env` and restarting the container has no effect on
its own. Reset the password directly instead:

```bash
docker exec -u root grafana grafana cli admin reset-admin-password '<new-password>'
```

(Keep `.env` in sync with whatever you set, so a from-scratch redeploy
matches.)

## Data & persistence

Every container's persistent state lives under `./data/<service>/` on the
host, which is entirely git-ignored — a fresh clone of this repo starts
with no service data, only configuration. `.env` is also git-ignored (only
`.env.example` is tracked) since it holds real passwords.
