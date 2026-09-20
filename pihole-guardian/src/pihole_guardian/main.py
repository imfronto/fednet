"""Process entry point: builds the shared context, starts the background
scheduler, and serves the API/dashboard. Run via `python -m pihole_guardian.main`.

Cleanup on shutdown is wired through FastAPI's own shutdown hook rather
than this module registering its own SIGTERM/SIGINT handlers — uvicorn
installs its own signal handlers when it runs the server, which would
silently replace anything registered here first, meaning the scheduler and
Pi-hole client would never get a clean shutdown on `docker stop`.
"""
from __future__ import annotations

import logging

import uvicorn

from pihole_guardian.api.app import create_app
from pihole_guardian.config import load_settings
from pihole_guardian.context import build_context
from pihole_guardian.logging_utils import configure_logging
from pihole_guardian.scheduler.jobs import build_scheduled_jobs
from pihole_guardian.scheduler.runner import Scheduler

logger = logging.getLogger(__name__)


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    logger.info("Starting pihole-guardian in %s mode", settings.mode.value)

    ctx = build_context(settings)
    scheduler = Scheduler(ctx, build_scheduled_jobs(settings))
    scheduler.start()

    def _on_shutdown() -> None:
        logger.info("Shutting down")
        scheduler.stop()
        ctx.close()

    app = create_app(ctx, on_shutdown=_on_shutdown)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_config=None)


if __name__ == "__main__":
    run()
