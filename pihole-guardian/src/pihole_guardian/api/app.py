"""FastAPI application factory. Deliberately not exposed to the internet
(requirement #16) — see docker-compose service notes and docs/CONFIGURATION.md
for the recommended LAN-only / reverse-proxy-with-auth deployment. Auth is
still enforced here regardless of network placement, as defense in depth.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pihole_guardian.api.dashboard import build_dashboard_router
from pihole_guardian.api.rate_limit import RateLimitMiddleware
from pihole_guardian.api.routes import build_api_router
from pihole_guardian.context import AppContext


def create_app(ctx: AppContext, on_shutdown: Callable[[], None] | None = None) -> FastAPI:
    ctx.settings.require_dashboard_secrets()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if on_shutdown:
            on_shutdown()

    app = FastAPI(title="Pi Guard", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=ctx.settings.rate_limit_per_minute)
    app.include_router(build_api_router(ctx), prefix="/api")
    app.include_router(build_dashboard_router(ctx))
    return app
