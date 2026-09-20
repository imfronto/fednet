"""In-memory per-client sliding-window rate limiter. Deliberately simple
(no Redis/external store) — this service has a handful of trusted LAN
clients (the dashboard's user, the CLI, maybe a homepage widget), not
public internet traffic, so a process-local counter that resets on restart
is an acceptable trade-off. See requirement #16's "rate limiting where
appropriate."
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, requests_per_minute: int) -> None:
        super().__init__(app)
        self._limit = requests_per_minute
        self._window_seconds = 60.0
        self._hits: dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        client_key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = self._hits[client_key]
        while bucket and now - bucket[0] > self._window_seconds:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        bucket.append(now)
        return await call_next(request)
