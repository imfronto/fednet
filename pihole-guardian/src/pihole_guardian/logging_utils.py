"""Structured logging. All log records that carry a domain/action/decision
context go through `log_event`, which emits JSON with a fixed set of fields
so downstream tooling (or just `jq`) can filter reliably. Secret-shaped
values are redacted defensively even though callers shouldn't be passing
them in the first place.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Any

_SECRET_KEY_PATTERN = re.compile(r"(password|token|secret|api_key|sid|csrf)", re.IGNORECASE)


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if _SECRET_KEY_PATTERN.search(str(k)) else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "event_fields", None)
        if extra:
            payload.update(_redact(extra))
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Emit a structured log line. Use this (not bare logger.info) for anything
    touching a domain/action/decision/change so every mutating event carries a
    consistent, greppable shape: timestamp, domain, action, classification,
    confidence, evidence, previous_state, new_state, change_id, error.
    """
    logger.log(level, message, extra={"event_fields": _redact(fields)})
