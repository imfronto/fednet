"""Runs every configured, available reputation provider for a domain,
enforcing a hard per-provider timeout so one slow/hanging backend can never
stall classification for every domain behind it. A provider that raises
unexpectedly (a bug, not a handled network error) is caught here too and
turned into an 'error' result — the aggregator's own contract to its caller
is that `gather` never raises and never silently drops a configured
provider's slot.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from pihole_guardian.reputation.base import ReputationProvider, ReputationResult

logger = logging.getLogger(__name__)


class ReputationAggregator:
    def __init__(self, providers: list[ReputationProvider], *, timeout_seconds: float = 8.0) -> None:
        self._providers = providers
        self._timeout = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=max(4, len(providers) or 1), thread_name_prefix="reputation")

    def gather(self, domain: str, registrable_domain: str) -> list[ReputationResult]:
        results: list[ReputationResult] = []
        futures: dict[Future, ReputationProvider] = {}
        for provider in self._providers:
            try:
                if not provider.is_available():
                    continue
            except Exception:
                continue
            futures[self._executor.submit(provider.check, domain, registrable_domain)] = provider

        for future, provider in futures.items():
            try:
                results.append(future.result(timeout=self._timeout))
            except FutureTimeoutError:
                logger.warning("Reputation provider %s timed out for %s", provider.name, domain)
                results.append(ReputationResult(provider.name, "error", 0.0, detail="timed out"))
            except Exception as exc:  # provider bug — never let it break classification
                logger.exception("Reputation provider %s raised for %s", provider.name, domain)
                results.append(ReputationResult(provider.name, "error", 0.0, detail=str(exc)))
        return results

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
