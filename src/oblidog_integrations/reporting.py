"""Report one-shot integration lifecycle around provider-specific work."""

from __future__ import annotations

import os
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from http.client import IncompleteRead, RemoteDisconnected
from urllib.error import HTTPError, URLError

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome produced by an adapter after all intended writes complete."""

    changes_detected: bool | None


IntegrationRunner = Callable[[], RunResult]


def _is_network_error(error: BaseException) -> bool:
    """Recognize transport failures through provider exception wrappers."""
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(
            error,
            (
                TimeoutError,
                ConnectionError,
                HTTPError,
                URLError,
                socket.gaierror,
                ssl.SSLError,
                IncompleteRead,
                RemoteDisconnected,
            ),
        ):
            return True
        if error.__cause__ is None:
            break
        error = error.__cause__
    return False


def run_with_reporting(integration: str, runner: IntegrationRunner) -> RunResult:
    """Run one adapter and log its outcome.

    Each adapter owns its category-scoped client and its SDK-managed run, so
    all provider synchronization uses the same client as lifecycle reporting.
    """
    with structlog.contextvars.bound_contextvars(integration=integration):
        try:
            result = runner()
            if not isinstance(result, RunResult):
                raise TypeError("Integration runner must return RunResult")
        except Exception as error:
            logger.error(
                "integration_run_failed",
                error_type=type(error).__name__,
                error=str(error),
                exc_info=os.getenv("OBLIDOG_LOG_TRACEBACKS", "0") == "1"
                or not _is_network_error(error),
            )
            raise
        logger.info(
            "integration_run_finished",
            result="success",
            changes_detected=result.changes_detected,
        )
        return result
