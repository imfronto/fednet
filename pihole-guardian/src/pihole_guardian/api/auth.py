"""Authentication for both surfaces this API exposes:
  - the JSON API: a bearer token (`GUARDIAN_API_TOKEN`), for the CLI-over-
    HTTP case and any external automation.
  - the HTML dashboard: HTTP Basic (`GUARDIAN_DASHBOARD_USERNAME`/
    `GUARDIAN_DASHBOARD_PASSWORD`), because it's what every browser already
    knows how to prompt for without any client-side code.

Both compare with `secrets.compare_digest` to avoid timing side-channels.
Per requirement #16, this service is not meant to be exposed to the
internet directly — see docs/CONFIGURATION.md — but auth is still enforced
unconditionally here rather than trusting network placement alone.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer

from pihole_guardian.config import Settings

_basic = HTTPBasic(auto_error=False)
_bearer = HTTPBearer(auto_error=False)


def require_api_token(settings: Settings):
    def _check(credentials=Depends(_bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials, settings.api_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")
        return True

    return _check


def require_dashboard_auth(settings: Settings):
    def _check(credentials: HTTPBasicCredentials | None = Depends(_basic)):
        valid_user = credentials and secrets.compare_digest(credentials.username, settings.dashboard_username)
        valid_pass = credentials and secrets.compare_digest(credentials.password, settings.dashboard_password)
        if not (valid_user and valid_pass):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid credentials", headers={"WWW-Authenticate": "Basic"}
            )
        return credentials.username

    return _check
