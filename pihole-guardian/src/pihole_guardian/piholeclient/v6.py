"""Pi-hole 6 FTL REST API client.

Verified against the actual OpenAPI spec shipped in the pi-hole/FTL repo
(src/api/docs/content/specs/*.yaml) rather than from memory:
  - POST /api/auth {"password": ...} -> session.sid / session.csrf / session.validity
  - Session id is sent back as the `X-FTL-SID` header (one of several
    equivalent options FTL accepts; this one avoids any ambiguity with
    cookies or a generic "sid" name colliding with something else).
  - /api/domains/{allow|deny}/{exact|regex}/{domain} for list management.
  - /api/action/gravity (POST, streamed text/plain) triggers `pihole -g`.
  - /api/queries with from/until/domain/cursor/length filters for the query log.

`get_version_info` and `get_adlists` parse defensively (`.get()` with
fallbacks) since they're used for reporting/health display only, never for
a safety-critical decision — an unexpected field name there should degrade
gracefully, not crash the collector loop.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from collections.abc import Iterator

import httpx

from pihole_guardian.piholeclient.base import PiholeClient
from pihole_guardian.piholeclient.exceptions import (
    PiholeAPIError,
    PiholeAuthError,
    PiholeConnectionError,
)
from pihole_guardian.piholeclient.models import DomainListEntry, PiholeVersionInfo, QueryRecord

logger = logging.getLogger(__name__)

_SID_HEADER = "X-FTL-SID"


class PiholeV6Client(PiholeClient):
    def __init__(
        self,
        base_url: str,
        password: str,
        *,
        verify_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._password = password
        self._sid: str | None = None
        self._session_expires_at: float = 0.0
        self._http = httpx.Client(
            base_url=self._base_url, verify=verify_tls, timeout=timeout, follow_redirects=False
        )

    # --- session handling ----------------------------------------------

    def _authenticate(self) -> None:
        try:
            resp = self._http.post("/api/auth", json={"password": self._password})
        except httpx.RequestError as exc:
            raise PiholeConnectionError(f"Could not reach Pi-hole at {self._base_url}: {exc}") from exc
        if resp.status_code not in (200, 401):
            raise PiholeAPIError(resp.status_code, "Unexpected response during authentication")
        data = resp.json()
        session = data.get("session", {})
        if not session.get("valid"):
            raise PiholeAuthError(session.get("message") or "Authentication rejected by Pi-hole")
        self._sid = session.get("sid")  # None is valid: means no auth required
        validity = session.get("validity") or 0
        # Refresh a bit before actual expiry to avoid racing a mid-request 401.
        self._session_expires_at = time.monotonic() + max(validity - 15, 5)

    def _ensure_session(self) -> None:
        if self._sid is None and self._session_expires_at == 0.0:
            self._authenticate()
        elif self._sid is not None and time.monotonic() >= self._session_expires_at:
            self._authenticate()

    def _headers(self) -> dict[str, str]:
        return {_SID_HEADER: self._sid} if self._sid else {}

    def _request(self, method: str, path: str, *, retry_on_auth_failure: bool = True, **kwargs) -> httpx.Response:
        self._ensure_session()
        try:
            resp = self._http.request(method, path, headers=self._headers(), **kwargs)
        except httpx.RequestError as exc:
            raise PiholeConnectionError(f"Network error calling Pi-hole {method} {path}: {exc}") from exc

        if resp.status_code == 401 and retry_on_auth_failure:
            logger.info("Pi-hole session rejected, re-authenticating once", extra={"event_fields": {"path": path}})
            self._authenticate()
            return self._request(method, path, retry_on_auth_failure=False, **kwargs)

        if resp.status_code == 401:
            raise PiholeAuthError("Pi-hole rejected the session even after re-authentication")

        if resp.status_code >= 400:
            key = None
            message = resp.text
            try:
                body = resp.json()
                err = body.get("error", {})
                key, message = err.get("key"), err.get("message", resp.text)
            except ValueError:
                pass
            raise PiholeAPIError(resp.status_code, message, key)

        return resp

    def close(self) -> None:
        self._http.close()

    # --- health / version -------------------------------------------------

    def health_check(self) -> bool:
        try:
            self._ensure_session()
            return True
        except (PiholeConnectionError, PiholeAuthError, PiholeAPIError):
            return False

    def get_version_info(self) -> PiholeVersionInfo:
        try:
            resp = self._request("GET", "/api/info/version")
            data = resp.json().get("version", {})
            core = (data.get("core") or {}).get("local", {}).get("version")
            ftl = (data.get("ftl") or {}).get("local", {}).get("version")
            web = (data.get("web") or {}).get("local", {}).get("version")
            return PiholeVersionInfo(core=core, ftl=ftl, web=web)
        except (PiholeConnectionError, PiholeAuthError, PiholeAPIError, ValueError, AttributeError) as exc:
            logger.warning("Could not determine Pi-hole version", extra={"event_fields": {"error": str(exc)}})
            return PiholeVersionInfo(core=None, ftl=None, web=None)

    # --- queries -----------------------------------------------------------

    def get_recent_queries(
        self,
        *,
        since_unix: float | None = None,
        until_unix: float | None = None,
        domain: str | None = None,
        cursor: int | None = None,
        length: int = 500,
    ) -> tuple[list[QueryRecord], int | None]:
        params: dict = {"length": length}
        if since_unix is not None:
            params["from"] = since_unix
        if until_unix is not None:
            params["until"] = until_unix
        if domain is not None:
            params["domain"] = domain
        if cursor is not None:
            params["cursor"] = cursor

        resp = self._request("GET", "/api/queries", params=params)
        body = resp.json()
        records = []
        for q in body.get("queries", []):
            client = q.get("client") or {}
            reply = q.get("reply") or {}
            records.append(
                QueryRecord(
                    id=q.get("id", 0),
                    time=q.get("time", 0.0),
                    qtype=q.get("type", ""),
                    domain=q.get("domain", ""),
                    status=q.get("status"),
                    client_ip=client.get("ip"),
                    client_name=client.get("name"),
                    reply_type=reply.get("type"),
                    upstream=q.get("upstream"),
                )
            )
        return records, body.get("cursor")

    # --- domain lists --------------------------------------------------

    def find_domain_entry(self, domain: str, list_type: str) -> DomainListEntry | None:
        encoded = urllib.parse.quote(domain, safe="")
        path = f"/api/domains/{list_type}/exact/{encoded}"
        resp = self._request("GET", path)
        entries = resp.json().get("domains", [])
        for e in entries:
            if e.get("domain") == domain:
                return DomainListEntry(
                    domain=e.get("domain"),
                    list_type=e.get("type", list_type),
                    kind=e.get("kind", "exact"),
                    comment=e.get("comment"),
                    enabled=bool(e.get("enabled", True)),
                    id=e.get("id"),
                )
        return None

    def add_domain(
        self, domain: str, list_type: str, *, comment: str, kind: str = "exact", enabled: bool = True
    ) -> DomainListEntry:
        path = f"/api/domains/{list_type}/{kind}"
        resp = self._request(
            "POST",
            path,
            json={"domain": domain, "comment": comment, "groups": [0], "enabled": enabled},
        )
        body = resp.json()
        entries = body.get("domains", [])
        for e in entries:
            if e.get("domain") == domain:
                return DomainListEntry(
                    domain=e.get("domain"),
                    list_type=e.get("type", list_type),
                    kind=e.get("kind", kind),
                    comment=e.get("comment"),
                    enabled=bool(e.get("enabled", True)),
                    id=e.get("id"),
                )
        # Pi-hole accepted the request but didn't echo the entry back for some
        # reason — treat as a soft success rather than crash the caller; the
        # subsequent verification read (ChangeManager re-reads state after
        # every mutation) will catch a genuine failure.
        return DomainListEntry(domain=domain, list_type=list_type, kind=kind, comment=comment, enabled=enabled)

    def remove_domain(self, domain: str, list_type: str, *, kind: str = "exact") -> None:
        encoded = urllib.parse.quote(domain, safe="")
        path = f"/api/domains/{list_type}/{kind}/{encoded}"
        try:
            self._request("DELETE", path)
        except PiholeAPIError as exc:
            if exc.status_code == 404:
                return  # already gone — treat as success (idempotent)
            raise

    def list_all_domain_entries(self) -> list[DomainListEntry]:
        resp = self._request("GET", "/api/domains")
        entries = resp.json().get("domains", [])
        return [
            DomainListEntry(
                domain=e.get("domain"),
                list_type=e.get("type"),
                kind=e.get("kind", "exact"),
                comment=e.get("comment"),
                enabled=bool(e.get("enabled", True)),
                id=e.get("id"),
            )
            for e in entries
            if e.get("domain") and e.get("type") in ("allow", "deny")
        ]

    # --- lists / gravity -----------------------------------------------

    def get_adlists(self) -> list[dict]:
        try:
            resp = self._request("GET", "/api/lists")
            return resp.json().get("lists", [])
        except (PiholeConnectionError, PiholeAuthError, PiholeAPIError) as exc:
            logger.warning("Could not fetch adlists", extra={"event_fields": {"error": str(exc)}})
            return []

    def trigger_gravity_update(self) -> Iterator[str]:
        self._ensure_session()
        try:
            with self._http.stream(
                "POST", "/api/action/gravity", headers=self._headers(), timeout=300.0
            ) as resp:
                if resp.status_code >= 400:
                    raise PiholeAPIError(resp.status_code, "Gravity update request rejected")
                for line in resp.iter_lines():
                    yield line
        except httpx.RequestError as exc:
            raise PiholeConnectionError(f"Network error during gravity update: {exc}") from exc
