"""Central configuration. Every value is sourced from the environment (or a
.env file for local development) — never hard-coded, per the project's
security requirements. Defaults are chosen to be the safest possible: AUDIT
mode, TLS verification on, dry-run-shaped thresholds.
"""
from __future__ import annotations

import enum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(str, enum.Enum):
    """Operating mode, ordered from safest to most autonomous.

    AUDIT: classify only, no decisions surfaced, no changes.
    DRY_RUN: compute full decisions and log exactly what would happen, no changes.
    RECOMMEND: compute decisions; block/unblock actions become pending
        recommendations that require explicit human approval (`service review`).
    ENFORCE: high-confidence decisions that pass every safety gate are applied
        automatically; everything else still falls back to RECOMMEND behavior.
    """

    AUDIT = "AUDIT"
    DRY_RUN = "DRY_RUN"
    RECOMMEND = "RECOMMEND"
    ENFORCE = "ENFORCE"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUARDIAN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Pi-hole integration ---
    pihole_url: str = Field(
        default="http://pihole:80",
        description="Base URL of the Pi-hole instance (scheme+host+port, no trailing /admin).",
    )
    pihole_password: str = Field(
        default="",
        description="Pi-hole admin/API password. Required unless the Pi-hole has auth disabled.",
    )
    pihole_tls_verify: bool = Field(
        default=True,
        description="Verify TLS certificates when talking to Pi-hole. Only disable for known-good "
        "self-signed internal deployments, and prefer adding the CA instead.",
    )
    pihole_timeout_seconds: float = 10.0

    # --- Operating mode & scheduling ---
    mode: Mode = Field(default=Mode.AUDIT, description="Safest mode is the default; raise deliberately.")
    poll_interval_seconds: int = Field(default=300, ge=30, description="How often to collect new DNS queries.")
    classify_interval_seconds: int = Field(default=600, ge=60)
    decide_interval_seconds: int = Field(default=900, ge=60)
    revalidation_interval_hours: int = Field(default=72, ge=1)
    disruption_check_interval_seconds: int = Field(default=120, ge=30)
    list_refresh_interval_hours: int = Field(default=24, ge=1)
    retention_days_observations: int = Field(default=180, ge=7)
    retention_days_changes: int = Field(default=730, ge=30)
    retention_hours_recent_buckets: int = Field(default=72, ge=6)
    recent_bucket_minutes: int = Field(default=5, ge=1, le=60)

    # --- Decision thresholds (all configurable, per requirement #5) ---
    # These four numbers are chosen to be jointly consistent, not independently
    # "reasonable": with the default cap and bonus, two sources at the cap sum
    # to 0.45+0.45+0.20=1.10 (clipped to 1.0), which clears the 0.90 block bar —
    # so `min_independent_sources_for_block=2` is a real, reachable requirement,
    # not a dead knob. A *single* source alone still tops out at 0.45, nowhere
    # near either threshold, which is what makes "one weak signal never blocks"
    # a property of the arithmetic rather than a threshold that happens to be
    # set high enough today. If you raise max_single_source_weight or
    # source_agreement_bonus, re-check that a single source still can't cross
    # block_confidence_threshold on its own.
    block_confidence_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    monitor_confidence_threshold: float = Field(default=0.40, ge=0.0, le=1.0)
    min_independent_sources_for_block: int = Field(default=2, ge=1)
    max_single_source_weight: float = Field(default=0.45, ge=0.05, le=1.0)
    source_agreement_bonus: float = Field(default=0.20, ge=0.0, le=0.5)
    conflict_penalty: float = Field(default=0.30, ge=0.0, le=1.0)

    # --- Disruption detection ---
    disruption_query_rate_spike_multiplier: float = Field(
        default=3.0,
        description="A blocked domain's post-block query rate must be at least this many times its "
        "pre-block baseline rate to be treated as a possible-disruption signal (retry-storm proxy).",
    )
    disruption_sibling_failure_rate_threshold: float = Field(
        default=0.5,
        description="Fraction of a dependency-linked sibling domain's post-block queries resolving "
        "NXDOMAIN/SERVFAIL that is treated as a corroborating possible-disruption signal.",
    )
    disruption_min_queries: int = Field(
        default=5, description="Minimum post-block query attempts before disruption scoring is trusted."
    )
    disruption_lookback_hours: int = Field(default=1)

    # --- Reputation providers ---
    urlhaus_enabled: bool = True
    rdap_enabled: bool = True
    tracker_list_enabled: bool = True
    tracker_list_urls: str = Field(
        default="https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
        description="Comma-separated hosts-format list URLs used as tracking/ad evidence sources.",
    )
    virustotal_api_key: str = Field(default="", description="Optional. Leave empty to disable VirusTotal lookups.")
    reputation_provider_timeout_seconds: float = 8.0

    # --- Privacy ---
    client_tracking_enabled: bool = Field(
        default=False, description="If true, store salted per-domain client counts. Off by default."
    )
    client_hash_salt: str = Field(default="", description="Required if client_tracking_enabled is true.")

    # --- Storage ---
    database_path: Path = Field(default=Path("/data/pihole-guardian/guardian.db"))

    # --- API / dashboard ---
    api_host: str = "0.0.0.0"
    api_port: int = 8930
    dashboard_username: str = Field(default="admin")
    dashboard_password: str = Field(default="", description="Required — no default. Set via env.")
    api_token: str = Field(default="", description="Bearer token for programmatic API access. Required — no default.")
    rate_limit_per_minute: int = Field(default=120, ge=1)

    # --- Logging ---
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json", description="'json' or 'text'.")

    # --- Protected list ---
    protected_domains_path: Path = Field(default=Path("/data/pihole-guardian/protected_domains.yml"))
    legitimate_infra_path: Path = Field(
        default=Path(__file__).parent / "resources" / "legitimate_infra.yml"
    )

    @field_validator("tracker_list_urls")
    @classmethod
    def _non_empty_urls(cls, v: str) -> str:
        return v.strip()

    def require_dashboard_secrets(self) -> None:
        """Called before the API server starts; fails loudly instead of running with
        an empty/default credential, which would otherwise silently expose the
        dashboard on whatever network it's attached to."""
        if not self.dashboard_password:
            raise RuntimeError(
                "GUARDIAN_DASHBOARD_PASSWORD is not set. Refusing to start the dashboard "
                "with no credential configured."
            )
        if not self.api_token:
            raise RuntimeError(
                "GUARDIAN_API_TOKEN is not set. Refusing to start the API with no bearer token configured."
            )


def load_settings() -> Settings:
    return Settings()
