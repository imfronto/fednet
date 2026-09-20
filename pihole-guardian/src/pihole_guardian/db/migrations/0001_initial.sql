-- Initial schema for pihole-guardian.
-- Timestamps are stored as ISO-8601 UTC strings (TEXT) for readability in
-- ad-hoc `sqlite3` inspection; confidence/weight/score columns are REAL in
-- [0,1]. See docs/ARCHITECTURE.md for the full data-model rationale.

CREATE TABLE domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL UNIQUE,
    unicode_domain TEXT NOT NULL,
    registrable_domain TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    query_count INTEGER NOT NULL DEFAULT 0,
    distinct_client_count INTEGER NOT NULL DEFAULT 0,
    pihole_status TEXT NOT NULL DEFAULT 'unlisted',      -- unlisted | allowed | denied
    pihole_list_managed_by TEXT,                          -- 'manual' | 'guardian' | NULL if unlisted
    active INTEGER NOT NULL DEFAULT 1,                    -- 0 once a domain hasn't been seen in a long time
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_domains_registrable ON domains(registrable_domain);
CREATE INDEX idx_domains_last_seen ON domains(last_seen);
CREATE INDEX idx_domains_pihole_status ON domains(pihole_status);

CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    observed_date TEXT NOT NULL,   -- YYYY-MM-DD (UTC)
    query_count INTEGER NOT NULL DEFAULT 0,
    nxdomain_count INTEGER NOT NULL DEFAULT 0,
    servfail_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    ok_count INTEGER NOT NULL DEFAULT 0,
    distinct_clients INTEGER NOT NULL DEFAULT 0,
    record_types TEXT,             -- JSON array, e.g. ["A","AAAA","HTTPS"]
    UNIQUE(domain_id, observed_date)
);
CREATE INDEX idx_observations_domain ON observations(domain_id);
CREATE INDEX idx_observations_date ON observations(observed_date);

-- Fine-grained (bucketed, client-identity-free) counters used only for
-- near-real-time disruption detection. Kept separate from `observations`
-- (daily rollups, longer retention, used for trend/revalidation context)
-- and purged aggressively on its own short retention window.
CREATE TABLE recent_buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    bucket_start TEXT NOT NULL,   -- ISO-8601 UTC, floored to the bucket interval
    ok_count INTEGER NOT NULL DEFAULT 0,
    nxdomain_count INTEGER NOT NULL DEFAULT 0,
    servfail_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(domain_id, bucket_start)
);
CREATE INDEX idx_recent_buckets_domain ON recent_buckets(domain_id);
CREATE INDEX idx_recent_buckets_start ON recent_buckets(bucket_start);

CREATE TABLE client_seen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    client_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(domain_id, client_hash)
);

CREATE TABLE evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    category TEXT,
    verdict TEXT NOT NULL,          -- malicious | suspicious | tracking | telemetry | unwanted | legitimate | unknown | error
    weight REAL NOT NULL,
    detail TEXT,
    observed_at TEXT NOT NULL
);
CREATE INDEX idx_evidence_domain ON evidence(domain_id);
CREATE INDEX idx_evidence_observed_at ON evidence(observed_at);

CREATE TABLE classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    legitimate_signals TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
    malicious_signals TEXT NOT NULL DEFAULT '[]',    -- JSON array of strings
    conflict INTEGER NOT NULL DEFAULT 0,
    reasoning TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_classifications_domain ON classifications(domain_id);
CREATE INDEX idx_classifications_created ON classifications(created_at);

CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    classification_id INTEGER REFERENCES classifications(id),
    action TEXT NOT NULL,            -- block | unblock | monitor | no_action | flag_for_review
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | applied | superseded
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);
CREATE INDEX idx_decisions_domain ON decisions(domain_id);
CREATE INDEX idx_decisions_status ON decisions(status);

CREATE TABLE changes (
    id TEXT PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id),
    decision_id INTEGER REFERENCES decisions(id),
    change_type TEXT NOT NULL,       -- add_deny | remove_deny | add_allow | remove_allow | gravity_update
    previous_state TEXT NOT NULL,    -- JSON snapshot
    new_state TEXT NOT NULL,         -- JSON snapshot (intended/applied)
    simulated INTEGER NOT NULL DEFAULT 0,   -- 1 for DRY_RUN changes that were never applied
    applied INTEGER NOT NULL DEFAULT 0,
    applied_at TEXT,
    rolled_back INTEGER NOT NULL DEFAULT 0,
    rolled_back_at TEXT,
    rollback_of TEXT REFERENCES changes(id),
    actor TEXT NOT NULL,             -- 'system' | 'user:<name>'
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_changes_domain ON changes(domain_id);
CREATE INDEX idx_changes_created ON changes(created_at);

CREATE TABLE protected_domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL UNIQUE,     -- exact domain, or '*.example.com' wildcard
    match_type TEXT NOT NULL,         -- exact | parent | wildcard
    reason TEXT,
    source TEXT NOT NULL DEFAULT 'manual',  -- manual | bundled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE auto_managed_domains (
    domain_id INTEGER PRIMARY KEY REFERENCES domains(id) ON DELETE CASCADE,
    classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_summary TEXT NOT NULL,
    date_added TEXT NOT NULL,
    last_verified TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    source_change_id TEXT REFERENCES changes(id)
);

CREATE TABLE incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id),
    change_id TEXT REFERENCES changes(id),
    incident_type TEXT NOT NULL DEFAULT 'possible_disruption',
    evidence TEXT NOT NULL,           -- JSON: nxdomain/servfail deltas, ratios, counts
    status TEXT NOT NULL DEFAULT 'open',  -- open | reviewed | dismissed
    auto_unblocked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_incidents_domain ON incidents(domain_id);
CREATE INDEX idx_incidents_status ON incidents(status);

CREATE TABLE configuration (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
