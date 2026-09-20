"""Wires together the pieces every entry point (scheduler, API, CLI) needs:
settings, a Pi-hole client, and the reputation-provider aggregator. Database
connections are deliberately NOT held here — see db/connection.py's
docstring for why each caller opens its own short-lived connection instead.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from pihole_guardian.config import Settings, load_settings
from pihole_guardian.db import migrator
from pihole_guardian.db.connection import connect
from pihole_guardian.piholeclient.base import PiholeClient
from pihole_guardian.piholeclient.v6 import PiholeV6Client
from pihole_guardian.reputation.aggregator import ReputationAggregator
from pihole_guardian.reputation.factory import build_providers
from pihole_guardian.safety import protected_list


@dataclass
class AppContext:
    settings: Settings
    pihole_client: PiholeClient
    aggregator: ReputationAggregator

    def connect(self) -> sqlite3.Connection:
        return connect(self.settings.database_path)

    def close(self) -> None:
        self.pihole_client.close()
        self.aggregator.shutdown()


def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or load_settings()

    conn = connect(settings.database_path)
    try:
        migrator.run_migrations(conn)
        protected_list.seed_from_file(conn, settings.protected_domains_path)
    finally:
        conn.close()

    pihole_client = PiholeV6Client(
        settings.pihole_url,
        settings.pihole_password,
        verify_tls=settings.pihole_tls_verify,
        timeout=settings.pihole_timeout_seconds,
    )
    aggregator = ReputationAggregator(
        build_providers(settings), timeout_seconds=settings.reputation_provider_timeout_seconds
    )
    return AppContext(settings=settings, pihole_client=pihole_client, aggregator=aggregator)
