"""Structured logging configuration shared by integration entry points."""

from __future__ import annotations

import os
from typing import Any

import structlog

_SECRET_MARKERS = (
    "API_KEY",
    "LOGIN",
    "PASSWORD",
    "PHONE",
    "SECRET",
    "TOKEN",
    "USERNAME",
)


def _redact_secrets(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Remove configured secrets from rendered events and tracebacks."""
    secrets = {
        value
        for name, value in os.environ.items()
        if value
        and len(value) >= 4
        and any(marker in name.upper() for marker in _SECRET_MARKERS)
    }

    def redact(value: Any) -> Any:
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        return value

    return {key: redact(value) for key, value in event_dict.items()}


def configure_logging() -> None:
    """Configure console logs, or JSON logs when requested by the runtime."""
    log_format = os.getenv("OBLIDOG_LOG_FORMAT", "console")
    if log_format == "console":
        renderer = structlog.dev.ConsoleRenderer()
    elif log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        raise ValueError("OBLIDOG_LOG_FORMAT must be either 'console' or 'json'")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_secrets,
            renderer,
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
