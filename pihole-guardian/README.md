# Pi Guard

A safety-first service that watches DNS queries observed by Pi-hole, checks
domains against multiple independent reputation sources, and maintains a
*separate, clearly-labeled* automatic blocklist — without ever touching
domains you manage yourself. (Technical package/repo name is
`pihole-guardian`/`pihole_guardian` — "Pi Guard" is the product name used
everywhere user-facing: the dashboard, the CLI banner, this doc.)

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design
rationale, database schema, threat model, and decision algorithm; this file
is the operational reference (setup, configuration, CLI, API, testing,
rollback, deployment).

## Setting this up on your own (not the fednet homelab)

If you just want to point this at your own Pi-hole — not integrate it into
this specific homelab repo — everything you need is self-contained in this
`pihole-guardian/` folder:

```
cd pihole-guardian
./setup.sh          # interactive terminal wizard — writes .env, tests your
                     # Pi-hole connection live, no dependencies beyond python3
docker compose build
docker compose up -d
```

The wizard (`setup_wizard.py`) is a single zero-dependency Python script —
it runs with a bare `python3`, before you've installed anything or built
any image, so anyone can clone this folder and have a working `.env` in a
couple of minutes. It walks through: your Pi-hole URL and password (with a
live connection test), dashboard credentials (offers to generate a short
one for you), the operating mode (defaults to the safest, `AUDIT`), and a
couple of optional extras. Run it again any time to regenerate `.env` — it
backs up the old one first.

The `docker-compose.yml` in this folder is the standalone deployment (binds
the dashboard to `127.0.0.1:8930` only, by default) — it's separate from
`services/pihole-guardian.yml` at the repo root, which is what the fednet
homelab itself uses via `make up`. Don't run both against the same Pi-hole
at once.

**The default mode is `AUDIT`: it observes and classifies but changes
nothing in Pi-hole until you deliberately raise the mode.** Read "Operating
modes" below before doing that.

## Contents

- [Setting this up on your own](#setting-this-up-on-your-own-not-the-fednet-homelab)
- [Quick start (fednet homelab integration)](#quick-start-fednet-homelab-integration)
- [Operating modes](#operating-modes)
- [Configuration reference](#configuration-reference)
- [CLI reference](#cli-reference)
- [API reference](#api-reference)
- [Dashboard](#dashboard)
- [Example decision report](#example-decision-report)
- [Rollback](#rollback)
- [Testing](#testing)
- [Backup and restore](#backup-and-restore)
- [Upgrading](#upgrading)
- [Known limitations / assumptions](#known-limitations--assumptions)

## Quick start (fednet homelab integration)

This is the other way to run it: integrated into this specific `homelab`
repo's stack rather than standalone (see above). It follows the repo's own
conventions — see the root `README.md`'s "Adding a new service" section.

1. Create the data directory and give it to the container's fixed non-root
   UID (Docker would otherwise create it as root-owned on first `up`, which
   the container can't write to — see the Dockerfile's UID 10001 note), and
   copy the example protected-domains file so it's editable:
   ```
   mkdir -p data/pihole-guardian
   sudo chown 10001:10001 data/pihole-guardian
   cp pihole-guardian/config/protected_domains.example.yml data/pihole-guardian/protected_domains.yml
   ```
2. In the repo root `.env`, set the two required secrets (see root
   `.env.example`'s "Pi-hole Guardian" section):
   ```
   GUARDIAN_DASHBOARD_PASSWORD=<a real password>
   GUARDIAN_API_TOKEN=<a random token, e.g. `openssl rand -hex 32`>
   ```
3. `make up` (the root Makefile globs `services/*.yml` automatically).
4. Check it started cleanly and is talking to Pi-hole:
   ```
   docker compose exec pihole-guardian pihole-guardian health
   docker compose exec pihole-guardian pihole-guardian status
   ```
5. Optionally look at the dashboard. It isn't published to a host port by
   default (see `services/pihole-guardian.yml`'s comment on why) — either
   uncomment the `ports:` line for LAN-only access during setup, or run:
   ```
   docker compose exec pihole-guardian pihole-guardian analyze
   ```
   to force one immediate collect+classify+decide cycle and inspect the
   result via the CLI (`pihole-guardian review`, `pihole-guardian explain <domain>`)
   without exposing anything.

Leave it in `AUDIT` mode for at least a few days and look at
`pihole-guardian review` / the dashboard's "Needs Review" filter before
considering `RECOMMEND`, and only move to `ENFORCE` once you've seen enough
`explain` output to trust the classifications on your own network.

## Operating modes

| Mode | Classifies? | Computes decisions? | Applies changes? |
|---|---|---|---|
| `AUDIT` (default) | yes | no (recorded as `observed` only) | never |
| `DRY_RUN` | yes | yes, fully | never — recorded as `simulated`, visible via `pihole-guardian changes` |
| `RECOMMEND` | yes | yes | only after explicit approval (`pihole-guardian review-approve <id>`) |
| `ENFORCE` | yes | yes | automatically, but **only** decisions clearing every safety gate (see `docs/ARCHITECTURE.md`); everything else still falls back to pending review |

Two things are true in *every* mode, including `ENFORCE`:
- A domain matching your `protected_domains` list, or the curated
  legitimate-infrastructure list, is never auto-blocked.
- Removing an *existing* automatic block (because revalidation or
  disruption detection determined it shouldn't remain) is treated as the
  safety-preferred direction and is not throttled the way new blocks are —
  except in `AUDIT`/`DRY_RUN`, where the "make no changes" guarantee is
  absolute and even a protective unblock waits for you to raise the mode
  (this is a deliberate trade-off — see `decision/engine.py`'s docstring).

The one exception to "mode governs everything": `safety/disruption.py`'s
auto-unblock in response to a possible-disruption signal always executes
immediately, regardless of mode, because it only ever reverses a mutation
this service already made with authorization — see that module's comment.

## Configuration reference

Every setting is an environment variable, `GUARDIAN_`-prefixed except where
noted. Never hard-coded, never logged (see `logging_utils.py`'s redaction).
The complete, individually-commented list is in
[`pihole-guardian/.env.example`](.env.example); the highlights:

| Variable | Default | Notes |
|---|---|---|
| `GUARDIAN_PIHOLE_URL` | `http://pihole:80` | |
| `GUARDIAN_PIHOLE_PASSWORD` | *(none — required)* | Reuse the root `.env`'s `PIHOLE_PASSWORD` |
| `GUARDIAN_MODE` | `AUDIT` | See table above |
| `GUARDIAN_DASHBOARD_PASSWORD` | *(none — required)* | Service refuses to start without it |
| `GUARDIAN_API_TOKEN` | *(none — required)* | Bearer token for the JSON API |
| `GUARDIAN_BLOCK_CONFIDENCE_THRESHOLD` | `0.90` | |
| `GUARDIAN_MONITOR_CONFIDENCE_THRESHOLD` | `0.40` | |
| `GUARDIAN_MIN_INDEPENDENT_SOURCES_FOR_BLOCK` | `2` | |
| `GUARDIAN_MAX_SINGLE_SOURCE_WEIGHT` | `0.45` | See the calibration note in `config.py` before changing |
| `GUARDIAN_SOURCE_AGREEMENT_BONUS` | `0.20` | ″ |
| `GUARDIAN_VIRUSTOTAL_API_KEY` | *(empty — disabled)* | Optional reputation provider |
| `GUARDIAN_CLIENT_TRACKING_ENABLED` | `false` | Stores salted per-domain client counts if enabled |
| `GUARDIAN_PIHOLE_TLS_VERIFY` | `true` | Only disable for a known-good internal deployment |

Protected domains are managed as data, not env vars — edit
`data/pihole-guardian/protected_domains.yml` (seeded from
`config/protected_domains.example.yml`) or use `pihole-guardian allow <domain>` /
the dashboard's "Protected" tab.

## CLI reference

Installed as `pihole-guardian` inside the container (not `service` — that
name is already a system command on Linux):

| Command | Effect |
|---|---|
| `pihole-guardian status` | Mode, Pi-hole reachability, key counts |
| `pihole-guardian health` | Exit 0/1 for health checks/monitoring |
| `pihole-guardian analyze` | Force one immediate collect+classify+decide cycle |
| `pihole-guardian review` | List decisions pending approval |
| `pihole-guardian review-approve <id>` | Approve a pending decision |
| `pihole-guardian review-reject <id>` | Reject a pending decision |
| `pihole-guardian explain <domain>` | Full reasoning trail (add `--json` for machine-readable output) |
| `pihole-guardian changes` | Recent changes with their change IDs |
| `pihole-guardian rollback <change-id>` | Reverse a previously applied change |
| `pihole-guardian allow <domain>` | Protect a domain permanently; unblocks it now if currently auto-blocked |
| `pihole-guardian unprotect <pattern>` | Remove a protected-domain entry |
| `pihole-guardian block <domain>` | Immediately block a domain (bypasses the classification pipeline; still refuses protected domains) |
| `pihole-guardian verify <domain>` | Run an on-demand reputation check right now |
| `pihole-guardian revalidate` | Force an immediate revalidation pass over every auto-managed domain |

Run any command with `--help` for its full option list.

## API reference

All endpoints under `/api/*` require `Authorization: Bearer <GUARDIAN_API_TOKEN>`.
The dashboard (everything else) requires HTTP Basic auth. Not exposed to a
host port by default — see `services/pihole-guardian.yml`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Unauthenticated liveness check |
| GET | `/api/status` | Mode, reachability, counts |
| GET | `/api/domains?filter=&q=` | Filtered domain list (`all/blocked/allowed/unknown/needs_review/recently_changed/high_risk`) |
| GET | `/api/domains/{domain}/explain` | Full reasoning trail, JSON |
| POST | `/api/domains/{domain}/verify` | On-demand reputation re-check |
| POST | `/api/domains/{domain}/block` | Manual block |
| POST | `/api/domains/{domain}/allow` | Manual allow + protect |
| GET / POST | `/api/protected` | List / add protected-domain entries |
| DELETE | `/api/protected/{pattern}` | Remove a protected-domain entry |
| GET | `/api/changes` | Recent changes |
| POST | `/api/changes/{id}/rollback` | Roll back a change |
| GET | `/api/review` | Pending decisions |
| POST | `/api/review/{id}/approve` \| `/reject` | Resolve a pending decision |
| GET | `/api/incidents` | Disruption incidents |
| POST | `/api/incidents/{id}/resolve` | Mark an incident reviewed |

## Dashboard

Server-rendered (Jinja2, no JS build step) at `/`. Filters match the
required set exactly: All, Blocked, Allowed, Unknown, Needs Review,
Recently Changed, High Risk. Every domain page lets you override the
automated decision (Block now / Allow & protect / Re-check now) and shows
its full evidence trail and change history with per-change rollback
buttons.

## Example decision report

Output of `pihole-guardian explain ads.trackervendor.example`:

```
Domain: ads.trackervendor.example
Status: DENIED  (managed by: guardian)

Classification: advertising_tracking
Confidence: 100%

Evidence:
  + [tracker_list] tracking: Present in bundled tracking/advertising hosts list
  + [virustotal] tracking: VirusTotal vendor categories indicate tracking/advertising
  + [behavioral_lexical] tracking: Hostname contains a token conventionally
    associated with tracking/telemetry infrastructure — weak signal, never
    sufficient alone
  - [rdap] legitimate: Registered 1842 days ago

Legitimate indicators:
  - rdap: Registered 1842 days ago

Decision: BLOCK
Reason: Confidence (100%) meets the block threshold (90%) with 3 independent
corroborating source(s) and no strong legitimate-service indicators. Dominant
signal type: tracking (from 3 independent source(s)).

Last verified: 2026-09-19T14:02:11+00:00
```

And a protected domain that evidence disagreed about — showing the
conflict-handling path, never silently resolved:

```
Domain: cdn.somevendor.example
Status: UNLISTED
Protected: yes (matches curated legitimate-infrastructure entry 'somevendor.example')

Classification: legitimate_infrastructure
Confidence: 100%
Conflict: evidence disagreed; confidence was reduced accordingly

Evidence:
  + [tracker_list] tracking: Present in bundled tracking/advertising hosts list
  - [legitimate_infra] legitimate: Matches curated legitimate-infrastructure entry

Legitimate indicators:
  - legitimate_infra: Matches curated legitimate-infrastructure entry 'somevendor.example' (cdn)

Decision: NO_ACTION
Reason: Domain matches curated legitimate-infrastructure evidence; never
eligible for automatic blocking.
```

## Rollback

Every mutation the service makes is ledgered before it happens
(`safety/change_manager.py`), with a change id you'll see in logs, the
`changes` CLI output, and the dashboard:

```
pihole-guardian changes
# 20260919T140211-a1b2c3d4  ads.trackervendor.example   add_deny  APPLIED  system  2026-09-19T14:02:11Z

pihole-guardian rollback 20260919T140211-a1b2c3d4
# Rolled back 20260919T140211-a1b2c3d4. New change: 20260919T151022-e5f6a7b8
```

Rollback re-reads the change's before/after snapshot and restores it
through the exact same guarded code path as any other mutation — it will
refuse (rather than guess) if the live Pi-hole state no longer matches what
the service expects (e.g. someone edited the entry by hand in the interim).
A change that was only `simulated` (DRY_RUN) or already rolled back cannot
be rolled back again; the CLI/API will tell you why.

## Testing

No real Pi-hole required — everything runs against an in-process fake
(`tests/fakes.py`) or, for the HTTP client specifically, a mocked transport
verified against the real Pi-hole 6 OpenAPI spec (`tests/test_pihole_v6_client.py`).

```
cd pihole-guardian
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

Coverage: legitimate domains, clearly malicious domains, unknown domains,
conflicting reputation results, reputation-provider outages, Pi-hole
outages, false-positive scenarios, protected domains, rollbacks, database
reliability, API authentication, rate limiting, revalidation, and
service-disruption detection — see `tests/` for the full list, one file per
concern.

## Backup and restore

The entire persistent state is `data/pihole-guardian/` (the SQLite database
and your `protected_domains.yml`) — it's covered by this repo's existing
`make backup` / `make restore` (restic) workflow automatically, since that
snapshots everything under `data/` already. No separate backup mechanism
needed. To restore just this service after a `make restore`, it's enough to
`docker compose restart pihole-guardian` — migrations are idempotent and
run automatically on startup.

## Upgrading

```
git pull
docker compose build pihole-guardian
docker compose up -d pihole-guardian
```

Database migrations in `db/migrations/` run automatically and are additive
(new numbered files only) — an upgrade never rewrites or drops an existing
migration. Check `docker compose logs pihole-guardian` after upgrading for
`Applied migration ...` lines.

## Known limitations / assumptions

Stated explicitly rather than left implicit — see `docs/ARCHITECTURE.md`'s
"Threat model" section for the full list. The two most likely to matter in
practice:

- Registrable-domain grouping uses `tldextract`'s bundled offline PSL
  snapshot, not a live-fetched one — it can lag slightly between
  `tldextract` releases.
- The disruption detector's evidence is correlational (query-rate spikes,
  sibling-domain failure rates), not proof of an application outage — it is
  designed to err toward unblocking too readily rather than too rarely.
- Only Pi-hole 6's REST API is implemented (`piholeclient/v6.py`). Version
  is detected and surfaced (`pihole-guardian status`), but there's no
  Pi-hole 5 (PHP `api.php`) implementation — the `PiholeClient` abstraction
  is what would let one be added without touching anything upstream of it,
  but that work hasn't been done here.
