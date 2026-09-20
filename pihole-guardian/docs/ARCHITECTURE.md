# Architecture

## Components and data flow

```
                    ┌─────────────────────────────────────────────┐
                    │              Pi-hole (v6 FTL API)            │
                    └───────────────▲───────────────────┬──────────┘
                                     │ read queries/lists │ add/remove domains,
                                     │                     │ trigger gravity
                     ┌───────────────┴──────┐    ┌────────▼─────────┐
                     │  collector/           │    │ safety/           │
                     │  query_collector.py   │    │ change_manager.py │  ◄── the ONLY
                     │  list_collector.py    │    │ (rollback ledger, │      code path
                     └───────────┬───────────┘    │  manual-entry     │      that mutates
                                 │                 │  guard)           │      Pi-hole
                                 ▼                 └────────▲──────────┘
                     ┌───────────────────────┐              │
                     │  SQLite (WAL mode)    │◄─────────────┘
                     │  domains, observations,│
                     │  recent_buckets,       │◄───────┐
                     │  evidence,             │        │
                     │  classifications,      │   ┌────┴─────────────┐
                     │  decisions, changes,   │   │ decision/engine.py│
                     │  protected_domains,    │◄──┤ (protection checks,│
                     │  auto_managed_domains, │   │  mode gate)        │
                     │  incidents             │   └────▲───────────────┘
                     └───────────┬────────────┘        │
                                 │                 ┌────┴──────────────────┐
                                 └────────────────►│ classification/engine │
                                                    │ (scoring, conflict    │
                     ┌──────────────────────┐      │  detection)           │
                     │ reputation/           │◄─────┤                       │
                     │ aggregator.py         │      └───────────────────────┘
                     │  ├─ legitimate_infra  │
                     │  ├─ tracker_lists     │
                     │  ├─ urlhaus           │
                     │  ├─ rdap              │
                     │  └─ virustotal (opt.) │
                     └──────────────────────┘

  scheduler/runner.py drives collect → classify → decide → revalidate →
  disruption_check → cleanup on independent intervals (threads, not asyncio —
  see scheduler/runner.py's docstring for why).

  api/app.py (FastAPI: JSON API + server-rendered dashboard) and cli/main.py
  (Typer) both call into the same services.py use-case functions and the
  same safety/change_manager.py — neither surface has its own copy of the
  decision logic.
```

## Database schema

SQLite, WAL mode, hand-written versioned SQL migrations (`db/migrations/*.sql`,
applied by `db/migrator.py`) rather than an ORM — see that file's docstring
for the reasoning. Tables:

| Table | Purpose |
|---|---|
| `domains` | One row per normalized domain: first/last seen, query count, current Pi-hole status, who manages it |
| `observations` | Daily rollups (query/NXDOMAIN/SERVFAIL/blocked counts) — never raw per-query rows |
| `recent_buckets` | Fine-grained (default 5-minute), short-retention counters used only for disruption detection |
| `client_seen` | Optional, off by default: salted per-domain client hashes (never raw IPs), only if `GUARDIAN_CLIENT_TRACKING_ENABLED=true` |
| `evidence` | Every reputation-provider/behavioral result ever recorded for a domain |
| `classifications` | Category + confidence + reasoning produced by the classification engine |
| `decisions` | Action (block/unblock/monitor/no_action/flag_for_review) + status (observed/simulated/pending/applied/...) |
| `changes` | The rollback ledger: before/after state snapshot, change id, applied/rolled-back flags |
| `protected_domains` | The manually managed, never-auto-block list |
| `auto_managed_domains` | Metadata for every domain this service itself added to Pi-hole's denylist |
| `incidents` | Possible-disruption events, correlational evidence, resolution status |
| `configuration` | Runtime key/value overrides (currently just the query-collector's incremental-fetch cursor) |

Full column-level detail is in the migration file itself — it's short and
meant to be read directly rather than duplicated here and left to drift.

## Threat model

**In scope / defended against:**
- A reputation provider going down, timing out, or returning garbage → must
  never be interpreted as "malicious" (`reputation/base.py`'s contract,
  enforced structurally by `aggregator.py` catching every exception).
- A single weak or wrong signal → capped mathematically below the block
  threshold (`classification/engine.py`), not just gated by a threshold that
  could be misconfigured.
- Accidentally blocking legitimate infrastructure → curated allow-list
  (`resources/legitimate_infra.yml`) plus the user's own `protected_domains`
  table both act as hard overrides checked *before* any scoring.
- Silently modifying or deleting a domain a human added manually to Pi-hole
  → `change_manager.py` verifies Pi-hole's live comment tag before ever
  touching an existing entry, independent of what the local database
  believes.
- An automated block causing an outage → `safety/disruption.py` watches for
  a post-block query-rate spike or sibling-domain failure rate and
  auto-unblocks (bypassing the mode gate — see that module's comment for
  why that specific exception is safe) while opening an incident for
  human review, and refuses to re-block until the incident is resolved.
- Losing track of what the service changed → every mutation goes through
  `change_manager.py`, which is the only code path allowed to call
  `PiholeClient.add_domain`/`remove_domain`, and every call there is
  ledgered with a rollback path.
- Dashboard/API left reachable by anyone on the network → bearer-token
  and HTTP-Basic auth enforced unconditionally (`api/auth.py`), the service
  refuses to start with unset credentials, and it is not published to a
  host port by default in `services/pihole-guardian.yml`.
- Sending private DNS query data to third parties → only the domain name
  (never client IP, never full query history) goes to reputation providers,
  and providers requiring an API key are off by default.

**Explicitly out of scope (stated, not silently assumed away):**
- Confirming an application outage with certainty from DNS data alone —
  the disruption detector produces *correlational* evidence for a human
  (see `safety/disruption.py`'s docstring), never a causal claim.
- A fully general behavioral dependency graph across arbitrary co-occurring
  domains — `classification/dependency.py` only covers same-registrable-
  domain relationships (e.g. `api.example.com` inheriting protection from
  `example.com`). A wider graph is a plausible future enhancement, not
  implemented here.
- Perfect Public-Suffix-List coverage for registrable-domain extraction —
  `domain_utils.py` uses `tldextract` pinned to its bundled offline
  snapshot (no live network fetch), which can lag the real PSL between
  `tldextract` releases.
- Protecting against a compromised Pi-hole instance itself, or a
  man-in-the-middle between this service and Pi-hole on the same trusted
  Docker network — TLS verification is available (`GUARDIAN_PIHOLE_TLS_VERIFY`)
  but the default deployment talks to Pi-hole over the internal `edge`
  Docker network, which is the trust boundary this service assumes.

## Decision-making algorithm

Two separate stages, deliberately not one score-to-action function:

**1. Classification** (`classification/engine.py`) answers "what does the
evidence say this domain is":
- Each negative-verdict source's weight is capped at
  `GUARDIAN_MAX_SINGLE_SOURCE_WEIGHT` (default 0.45) before summing.
- Two or more independent sources agreeing adds a flat
  `GUARDIAN_SOURCE_AGREEMENT_BONUS` (default 0.20).
- These two defaults are chosen jointly: two sources at the cap
  (0.45 + 0.45 + 0.20 = 1.10, clipped to 1.0) clear the 0.90 block bar; one
  source alone (0.45) comes nowhere close to either threshold. That's what
  makes "one weak signal never blocks" a property of the arithmetic, not
  just a threshold that happens to be set high enough today.
- Any legitimate-verdict evidence coexisting with negative evidence sets
  `conflict=True` and subtracts `GUARDIAN_CONFLICT_PENALTY` from the
  negative score — conflicting evidence produces a *less* confident answer,
  never a silently resolved one.
- Query volume/popularity is read nowhere in this file — see
  `classification/behavioral.py`'s docstring for the explicit statement of
  what it deliberately does not do.

**2. Decision** (`decision/engine.py`) answers "what, if anything, should
happen", as an ordered set of checks:
1. Manual `protected_domains` membership wins, unconditionally, before
   anything else is even inspected.
2. A hard legitimate-infrastructure classification match wins next.
3. Conflicting evidence → `flag_for_review`, never `block`.
4. Blocking additionally requires
   `GUARDIAN_MIN_INDEPENDENT_SOURCES_FOR_BLOCK` (default 2) distinct
   corroborating sources — a structural floor independent of the confidence
   math above.
5. Below the monitor threshold, nothing happens — unknown is not treated
   as guilty.

`decision.engine.gate_for_mode` is a *third*, independent step: given the
action the evidence warrants, it asks whether the operator's configured
mode (AUDIT/DRY_RUN/RECOMMEND/ENFORCE) actually allows it to happen now, or
requires a human first. Raising the mode can never change what the system
*believes* about a domain — only what it's allowed to *do* about that
belief. See `docs/../README.md`'s "Operating modes" section for the exact
behavior of each mode.

## False-positive prevention strategy

In order of how early they short-circuit everything else:

1. **Manual protected list** (`protected_domains` table, seeded from
   `config/protected_domains.example.yml`) — absolute, checked first,
   never overridden by any amount of negative evidence.
2. **Curated legitimate-infrastructure list**
   (`resources/legitimate_infra.yml`) — OS vendors, package managers, CDNs,
   auth providers, cert/NTP/DNS infra, etc. A match here forces the
   classification itself to `legitimate_infrastructure` regardless of any
   other evidence.
3. **Dependency inheritance** (`classification/dependency.py`) — a domain
   sharing a registrable domain with an already-allowed or already-
   legitimate domain gets a weak protective signal (e.g. `api.example.com`
   benefiting from `example.com` being protected).
4. **Independent-source requirement** — even overwhelming evidence from a
   single source cannot reach the block threshold (see the calibration
   note above).
5. **Conflict handling** — any legitimate signal alongside negative
   evidence caps the outcome at `flag_for_review`.
6. **Revalidation** (`scheduler/jobs.py::revalidate_job`, default every 72h)
   — every auto-blocked domain is re-checked from scratch; if the evidence
   that justified the block no longer holds, the block is removed and the
   change is ledgered like any other.
7. **Disruption detection** (`safety/disruption.py`) — a last-resort safety
   net that reverses a block within minutes if it looks associated with a
   query-rate spike or sibling-domain failures, independent of whether the
   original classification was "correct" by any other measure.
