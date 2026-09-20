"""Requirement #19: API authentication and rate limiting.

Uses FastAPI's TestClient (in-process ASGI, no real network) against an
AppContext wired to the FakePiholeClient — no real Pi-hole or external
services involved.
"""
from fastapi.testclient import TestClient

from pihole_guardian.api.app import create_app
from pihole_guardian.context import AppContext
from pihole_guardian.db.connection import connect
from pihole_guardian.db.migrator import run_migrations
from pihole_guardian.reputation.aggregator import ReputationAggregator


def _client(settings, fake_pihole) -> TestClient:
    # Routes open their own connections against settings.database_path on
    # demand (see context.py's docstring on why) — unlike most other test
    # modules this one doesn't use the `conn` fixture, so the schema needs
    # to be migrated explicitly here first.
    migration_conn = connect(settings.database_path)
    run_migrations(migration_conn)
    migration_conn.close()

    ctx = AppContext(settings=settings, pihole_client=fake_pihole, aggregator=ReputationAggregator([]))
    app = create_app(ctx)
    return TestClient(app)


def test_health_endpoint_requires_no_auth(settings, fake_pihole):
    client = _client(settings, fake_pihole)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_status_endpoint_requires_bearer_token(settings, fake_pihole):
    client = _client(settings, fake_pihole)
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    resp = client.get("/api/status", headers={"Authorization": f"Bearer {settings.api_token}"})
    assert resp.status_code == 200


def test_dashboard_requires_basic_auth(settings, fake_pihole):
    client = _client(settings, fake_pihole)
    assert client.get("/").status_code == 401
    resp = client.get("/", auth=(settings.dashboard_username, settings.dashboard_password))
    assert resp.status_code == 200
    resp_wrong = client.get("/", auth=(settings.dashboard_username, "wrong-password"))
    assert resp_wrong.status_code == 401


def test_missing_dashboard_secrets_refuses_to_start(settings, fake_pihole):
    settings_no_secrets = settings.model_copy(update={"dashboard_password": "", "api_token": ""})
    ctx = AppContext(settings=settings_no_secrets, pihole_client=fake_pihole, aggregator=ReputationAggregator([]))
    try:
        create_app(ctx)
        assert False, "expected RuntimeError for missing dashboard secrets"
    except RuntimeError:
        pass


def test_rate_limit_returns_429_once_exceeded(settings, fake_pihole):
    settings = settings.model_copy(update={"rate_limit_per_minute": 3})
    client = _client(settings, fake_pihole)
    for _ in range(3):
        assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 429
