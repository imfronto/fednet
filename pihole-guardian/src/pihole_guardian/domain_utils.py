"""Domain normalization and matching helpers.

Every domain that enters the system (from Pi-hole's query log, from a
reputation provider, from a human typing into the CLI/dashboard) is passed
through `normalize_domain` first, so the rest of the codebase can assume a
single canonical form: lowercase, punycode-encoded (ASCII), no trailing dot,
no leading/trailing whitespace.

Registrable-domain (eTLD+1) extraction uses `tldextract` pinned to its
bundled offline Public Suffix List snapshot (`suffix_list_urls=()`) so this
never makes a network call and never depends on live connectivity — at the
cost of the snapshot going stale between `tldextract` releases. That's a
deliberate, documented trade-off (see docs/ARCHITECTURE.md): it is used only
for *grouping* domains under one registrable owner (e.g. protected-list
"parent domain" matching, and same-registrable-domain dependency evidence),
never as the sole basis for a block/unblock decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import tldextract

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

_VALID_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class NormalizedDomain:
    raw: str
    domain: str  # canonical ASCII/punycode form, e.g. "xn--4ca.com"
    unicode: str  # best-effort human-readable form, e.g. "ä.com"
    registrable_domain: str  # eTLD+1, e.g. "example.com"
    valid: bool
    error: str | None = None


def normalize_domain(raw: str) -> NormalizedDomain:
    original = raw
    try:
        candidate = raw.strip().lower().rstrip(".")
        if candidate.startswith("*."):
            candidate = candidate[2:]
        if not candidate or " " in candidate:
            return NormalizedDomain(original, "", "", "", False, "empty or contains whitespace")

        try:
            ascii_form = candidate.encode("idna").decode("ascii")
        except UnicodeError:
            # Already ASCII/punycode, or contains characters idna codec rejects
            # (e.g. a lone wildcard/underscore label from internal DNS) — fall
            # back to the raw candidate rather than failing normalization outright.
            ascii_form = candidate

        labels = ascii_form.split(".")
        if len(labels) < 2 or not all(_VALID_LABEL.match(lbl) for lbl in labels):
            return NormalizedDomain(original, ascii_form, candidate, "", False, "not a well-formed domain")

        try:
            unicode_form = ascii_form.encode("ascii").decode("idna")
        except (UnicodeError, UnicodeDecodeError):
            unicode_form = ascii_form

        extracted = _extract(ascii_form)
        registrable = (
            f"{extracted.domain}.{extracted.suffix}" if extracted.domain and extracted.suffix else ascii_form
        )

        return NormalizedDomain(original, ascii_form, unicode_form, registrable, True, None)
    except Exception as exc:  # normalization must never raise into the collector loop
        return NormalizedDomain(original, original.strip().lower(), original, "", False, str(exc))


def is_subdomain_or_equal(domain: str, parent: str) -> bool:
    domain = domain.rstrip(".")
    parent = parent.rstrip(".")
    return domain == parent or domain.endswith("." + parent)


def matches_wildcard(domain: str, pattern: str) -> bool:
    """pattern like '*.example.com' matches any subdomain (not the bare apex)."""
    if not pattern.startswith("*."):
        return domain == pattern
    suffix = pattern[1:]  # ".example.com"
    return domain.endswith(suffix) and domain != suffix[1:]
