"""Report one-shot integration lifecycle around provider-specific work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome produced by an adapter after all intended writes complete."""

    changes_detected: bool | None


IntegrationRunner = Callable[[], RunResult]


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
            logger.exception(
                "integration_run_failed",
                error_type=type(error).__name__,
            )
            raise
        logger.info(
            "integration_run_finished",
            result="success",
            changes_detected=result.changes_detected,
        )
        return result
