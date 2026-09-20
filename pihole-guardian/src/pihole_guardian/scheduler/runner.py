"""A deliberately simple background scheduler: one daemon thread per job,
each looping on its own interval. No external scheduling library — every
job is a plain synchronous function (see jobs.py), and the only
coordination needed is "run this every N seconds and don't let one bad
iteration kill the loop," which a `while` + `sleep` + `try/except` gets
without pulling in APScheduler's persistence/misfire machinery this
service doesn't need.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from pihole_guardian.context import AppContext

logger = logging.getLogger(__name__)


@dataclass
class ScheduledJob:
    name: str
    interval_seconds: float
    fn: Callable[[AppContext], None]


class Scheduler:
    def __init__(self, ctx: AppContext, jobs: list[ScheduledJob]) -> None:
        self._ctx = ctx
        self._jobs = jobs
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for job in self._jobs:
            t = threading.Thread(target=self._run_loop, args=(job,), name=f"guardian-{job.name}", daemon=True)
            t.start()
            self._threads.append(t)
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)

    def _run_loop(self, job: ScheduledJob) -> None:
        # Stagger the very first run slightly per job so they don't all fire
        # simultaneously at process start and contend for the DB/Pi-hole at once.
        self._stop.wait(min(5.0, job.interval_seconds / 10))
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                job.fn(self._ctx)
            except Exception:
                logger.exception("Job '%s' failed unexpectedly", job.name)
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, job.interval_seconds - elapsed))
