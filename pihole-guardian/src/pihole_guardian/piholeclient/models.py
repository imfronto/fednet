from __future__ import annotations

from dataclasses import dataclass

# Pi-hole 6 query `status` values that mean "resolved successfully" (forwarded
# upstream or served from cache) as opposed to blocked-by-pihole or failed.
# Kept as prefixes/sets rather than a hard enum because FTL has added new
# status strings across releases and an unrecognized one must degrade to
# "unknown", never crash the collector.
STATUS_BLOCKED_PREFIXES = ("GRAVITY", "DENYLIST", "REGEX", "EXTERNAL_BLOCKED", "SPECIAL_DOMAIN")
STATUS_OK_PREFIXES = ("FORWARDED", "CACHE", "RETRIED")


@dataclass
class QueryRecord:
    id: int
    time: float
    qtype: str
    domain: str
    status: str | None
    client_ip: str | None
    client_name: str | None
    reply_type: str | None
    upstream: str | None

    @property
    def was_blocked_by_pihole(self) -> bool:
        return bool(self.status) and self.status.startswith(STATUS_BLOCKED_PREFIXES)

    @property
    def resolved_ok(self) -> bool:
        return bool(self.status) and self.status.startswith(STATUS_OK_PREFIXES)

    @property
    def is_nxdomain(self) -> bool:
        return self.reply_type == "NXDOMAIN"

    @property
    def is_servfail(self) -> bool:
        return self.reply_type == "SERVFAIL"


@dataclass
class DomainListEntry:
    domain: str
    list_type: str  # "allow" | "deny"
    kind: str  # "exact" | "regex"
    comment: str | None
    enabled: bool
    id: int | None = None


@dataclass
class PiholeVersionInfo:
    core: str | None
    ftl: str | None
    web: str | None
    api_generation: str = "v6"
