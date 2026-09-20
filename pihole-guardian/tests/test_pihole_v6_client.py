"""Verifies the real HTTP client against a mocked transport — this is the
'integration test using a mock Pi-hole API' requirement (#19). It checks
the actual wire behavior (auth flow, session header, endpoint paths) against
what was confirmed from the real pi-hole/FTL OpenAPI spec, so a regression
here would mean the client no longer speaks the protocol Pi-hole 6 expects.
"""
from __future__ import annotations

import json

import httpx
import pytest

from pihole_guardian.piholeclient.exceptions import PiholeAuthError
from pihole_guardian.piholeclient.v6 import PiholeV6Client


class MockPiholeServer:
    def __init__(self, password: str = "correct-horse"):
        self.password = password
        self.sid = "test-session-id"
        self.deny: dict[str, dict] = {}
        self.auth_calls = 0
        self.reject_next_auth_use = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth" and request.method == "POST":
            self.auth_calls += 1
            body = json.loads(request.content or b"{}")
            if body.get("password") != self.password:
                return httpx.Response(401, json={"session": {"valid": False, "sid": None, "totp": False, "validity": -1, "message": "password incorrect"}})
            return httpx.Response(
                200,
                json={"session": {"valid": True, "sid": self.sid, "csrf": "csrf-token", "totp": False, "validity": 300, "message": "correct password"}},
            )

        sid_header = request.headers.get("X-FTL-SID")
        if self.reject_next_auth_use:
            self.reject_next_auth_use = False
            return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "session expired"}})
        if sid_header != self.sid:
            return httpx.Response(401, json={"error": {"key": "unauthorized", "message": "no/invalid session"}})

        if path.startswith("/api/domains/deny/exact/") and request.method == "GET":
            domain = path.rsplit("/", 1)[-1]
            entries = [self.deny[domain]] if domain in self.deny else []
            return httpx.Response(200, json={"domains": entries, "took": 0.001})

        if path == "/api/domains/deny/exact" and request.method == "POST":
            body = json.loads(request.content)
            entry = {"domain": body["domain"], "type": "deny", "kind": "exact", "comment": body.get("comment"), "groups": [0], "enabled": True, "id": 1, "date_added": 0, "date_modified": 0}
            self.deny[body["domain"]] = entry
            return httpx.Response(201, json={"domains": [entry], "processed": None, "took": 0.001})

        if path.startswith("/api/domains/deny/exact/") and request.method == "DELETE":
            domain = path.rsplit("/", 1)[-1]
            if domain in self.deny:
                del self.deny[domain]
                return httpx.Response(204)
            return httpx.Response(404, json={"took": 0.001})

        if path == "/api/action/gravity" and request.method == "POST":
            return httpx.Response(200, text="[i] Neutrino emissions detected...\n[✓] Pi-hole blocking is enabled\n")

        return httpx.Response(404, json={"error": {"key": "not_found", "message": "no such route in mock"}})


@pytest.fixture
def mock_server():
    return MockPiholeServer()


@pytest.fixture
def client(mock_server) -> PiholeV6Client:
    c = PiholeV6Client("http://pihole.test", mock_server.password, verify_tls=False)
    c._http = httpx.Client(
        base_url="http://pihole.test", transport=httpx.MockTransport(mock_server.handle)
    )
    return c


def test_authenticates_and_sends_session_header(client, mock_server):
    entry = client.find_domain_entry("nowhere.example", "deny")
    assert entry is None
    assert mock_server.auth_calls == 1


def test_wrong_password_raises_auth_error(mock_server):
    c = PiholeV6Client("http://pihole.test", "wrong-password", verify_tls=False)
    c._http = httpx.Client(base_url="http://pihole.test", transport=httpx.MockTransport(mock_server.handle))
    with pytest.raises(PiholeAuthError):
        c.find_domain_entry("example.com", "deny")


def test_add_and_find_domain_entry(client):
    client.add_domain("bad.example", "deny", comment="pihole-guardian:known_malicious")
    entry = client.find_domain_entry("bad.example", "deny")
    assert entry is not None
    assert entry.comment == "pihole-guardian:known_malicious"


def test_remove_nonexistent_domain_is_idempotent_not_an_error(client):
    client.remove_domain("never-added.example", "deny")  # must not raise


def test_expired_session_triggers_single_reauth_and_retry(client, mock_server):
    client.find_domain_entry("x.example", "deny")  # establish session
    assert mock_server.auth_calls == 1
    mock_server.reject_next_auth_use = True
    entry = client.find_domain_entry("x.example", "deny")  # should transparently re-auth
    assert entry is None
    assert mock_server.auth_calls == 2


def test_gravity_update_streams_output(client):
    lines = list(client.trigger_gravity_update())
    assert any("blocking is enabled" in line for line in lines)
