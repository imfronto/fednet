from __future__ import annotations

from pihole_guardian.config import Settings
from pihole_guardian.reputation.base import ReputationProvider
from pihole_guardian.reputation.legitimate_infra import LegitimateInfraProvider
from pihole_guardian.reputation.rdap import RDAPProvider
from pihole_guardian.reputation.tracker_lists import TrackerListProvider
from pihole_guardian.reputation.urlhaus import URLhausProvider
from pihole_guardian.reputation.virustotal import VirusTotalProvider


def build_providers(settings: Settings) -> list[ReputationProvider]:
    providers: list[ReputationProvider] = [LegitimateInfraProvider(settings.legitimate_infra_path)]

    if settings.tracker_list_enabled:
        urls = [u.strip() for u in settings.tracker_list_urls.split(",") if u.strip()]
        tracker_provider = TrackerListProvider(urls, refresh_interval_hours=settings.list_refresh_interval_hours)
        tracker_provider.refresh(force=True)
        providers.append(tracker_provider)

    if settings.urlhaus_enabled:
        providers.append(URLhausProvider(timeout=settings.reputation_provider_timeout_seconds))

    if settings.rdap_enabled:
        providers.append(RDAPProvider(timeout=settings.reputation_provider_timeout_seconds))

    providers.append(
        VirusTotalProvider(
            api_key=settings.virustotal_api_key, timeout=settings.reputation_provider_timeout_seconds
        )
    )

    return providers
