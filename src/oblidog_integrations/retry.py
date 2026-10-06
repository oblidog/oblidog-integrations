"""Bounded retries for read-only provider operations."""

import random
import socket
import ssl
import time
from collections.abc import Callable
from http.client import IncompleteRead, RemoteDisconnected
from urllib.error import HTTPError, URLError

import structlog

logger = structlog.get_logger(__name__)


class TransientProviderError(RuntimeError):
    """A provider-specific condition that may disappear on a fresh attempt."""


def is_transient(error: Exception) -> bool:
    """Classify network failures, including explicitly chained provider errors."""
    if isinstance(error, HTTPError):
        return 500 <= error.code < 600
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, URLError):
        return isinstance(error.reason, Exception) and is_transient(error.reason)
    if isinstance(
        error,
        (
            TimeoutError,
            ConnectionError,
            RemoteDisconnected,
            IncompleteRead,
            socket.gaierror,
            TransientProviderError,
        ),
    ):
        return True
    return isinstance(error.__cause__, Exception) and is_transient(error.__cause__)


def _source_error(error: Exception) -> Exception:
    """Unwrap transport errors without logging their potentially sensitive text."""
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, HTTPError):
            return error
        if isinstance(error, URLError) and isinstance(error.reason, Exception):
            error = error.reason
        elif isinstance(error.__cause__, Exception):
            error = error.__cause__
        else:
            break
    return error


def retry_provider[T](
    operation: Callable[[], T], *, integration: str, operation_name: str
) -> T:
    """Try at most three times, waiting 2/4 seconds plus up to one second jitter.

    Log only error types, never request URLs, credentials or response bodies.
    The final original exception propagates to the existing run reporting.
    """
    for attempt in range(1, 4):
        try:
            return operation()
        except Exception as error:
            if attempt == 3 or not is_transient(error):
                raise
            delay = 2**attempt + random.uniform(0, 1)
            source = _source_error(error)
            logger.warning(
                "request_retry",
                integration=integration,
                operation=operation_name,
                attempt=attempt + 1,
                max_attempts=3,
                reason=type(error).__name__,
                source_error_type=type(source).__name__,
                http_status=source.code if isinstance(source, HTTPError) else None,
                delay_seconds=round(delay, 3),
            )
            time.sleep(delay)
    raise AssertionError("unreachable")
