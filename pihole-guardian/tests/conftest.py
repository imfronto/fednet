from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pihole_guardian.config import Mode, Settings
from pihole_guardian.db.connection import connect
from pihole_guardian.db.migrator import run_migrations
from tests.fakes import FakePiholeClient


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "guardian.db",
        dashboard_username="admin",
        dashboard_password="test-password",
        api_token="test-token",
        mode=Mode.AUDIT,
        tracker_list_enabled=False,  # avoid real network calls in unit tests
        urlhaus_enabled=False,
        rdap_enabled=False,
        virustotal_api_key="",
        block_confidence_threshold=0.9,
        monitor_confidence_threshold=0.4,
        min_independent_sources_for_block=2,
        max_single_source_weight=0.45,
        source_agreement_bonus=0.20,
        conflict_penalty=0.30,
        legitimate_infra_path=Path(__file__).parent.parent
        / "src"
        / "pihole_guardian"
        / "resources"
        / "legitimate_infra.yml",
        protected_domains_path=tmp_path / "protected_domains.yml",  # deliberately absent unless a test writes it
    )


@pytest.fixture
def conn(settings: Settings) -> sqlite3.Connection:
    c = connect(settings.database_path)
    run_migrations(c)
    yield c
    c.close()


@pytest.fixture
def fake_pihole() -> FakePiholeClient:
    return FakePiholeClient()
