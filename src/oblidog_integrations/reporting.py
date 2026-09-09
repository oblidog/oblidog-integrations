"""Report one-shot integration lifecycle around provider-specific work."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import structlog
from oblidog_client import (
    IntegrationResult,
    IntegrationRunError,
    OblidogClient,
)

logger = structlog.get_logger(__name__)

_RETRY_DELAYS_SECONDS = (1.0, 3.0)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Outcome produced by an adapter after all intended writes complete."""

    changes_detected: bool | None


IntegrationRunner = Callable[[], RunResult]


def _required_env(name: str) -> str:
    if value := os.getenv(name):
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) else None


def _is_retryable(error: Exception) -> bool:
    status_code = _status_code(error)
    return isinstance(error, httpx.TransportError) or (
        status_code is not None and (status_code == 429 or status_code >= 500)
    )


def _call_with_retries[ResultT](
    operation: str,
    callback: Callable[[], ResultT],
) -> ResultT:
    attempts = len(_RETRY_DELAYS_SECONDS) + 1
    for attempt in range(1, attempts + 1):
        try:
            return callback()
        except Exception as error:
            if not _is_retryable(error) or attempt == attempts:
                raise
            delay = _RETRY_DELAYS_SECONDS[attempt - 1]
            logger.warning(
                "integration_report_retry",
                operation=operation,
                attempt=attempt,
                next_attempt=attempt + 1,
                delay_seconds=delay,
                error_type=type(error).__name__,
                status_code=_status_code(error),
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def _reported_error(error: Exception) -> IntegrationRunError:
    return IntegrationRunError(
        code="runner_failed",
        message=f"{type(error).__name__}: integration failed; inspect runner logs",
    )


def run_with_reporting(integration: str, runner: IntegrationRunner) -> RunResult | None:
    """Run an adapter, optionally reporting start and completion to Ledger.

    Without ``OBLIDOG_INTEGRATION_KEY`` this is the legacy unmonitored path.
    Reporting retries reuse the same run ID and request arguments. A disabled
    instance is skipped before provider work starts.
    """
    integration_key = os.getenv("OBLIDOG_INTEGRATION_KEY")
    if not integration_key:
        logger.info(
            "integration_reporting_disabled",
            integration=integration,
            reason="OBLIDOG_INTEGRATION_KEY_not_set",
        )
        try:
            return runner()
        except Exception as error:
            logger.exception(
                "integration_run_failed",
                integration=integration,
                reporting_enabled=False,
                error_type=type(error).__name__,
            )
            raise

    run_id = uuid.uuid4()
    with (
        structlog.contextvars.bound_contextvars(
            integration=integration,
            integration_key=integration_key,
            run_id=str(run_id),
        ),
        OblidogClient(
            base_url=_required_env("OBLIDOG_URL"),
            api_key=_required_env("OBLIDOG_API_KEY"),
        ) as client,
    ):
        try:
            instance = _call_with_retries(
                "get",
                lambda: client.integrations.get(integration_key),
            )
        except Exception as error:
            logger.error(
                "integration_instance_read_failed",
                error_type=type(error).__name__,
                status_code=_status_code(error),
            )
            raise

        if instance.provider != integration:
            logger.error(
                "integration_provider_mismatch",
                configured_provider=instance.provider,
            )
            raise RuntimeError(
                f"Integration provider mismatch: expected {integration!r}, "
                f"got {instance.provider!r}"
            )

        if not instance.enabled:
            logger.info("integration_run_skipped", reason="instance_disabled")
            return None

        try:
            _call_with_retries(
                "start",
                lambda: client.integrations.start(
                    integration_key,
                    run_id=run_id,
                    expected_revision=instance.revision,
                ),
            )
        except Exception as error:
            logger.error(
                "integration_start_report_failed",
                error_type=type(error).__name__,
                status_code=_status_code(error),
            )
            raise

        logger.info("integration_run_started")
        try:
            result = runner()
            if not isinstance(result, RunResult):
                raise TypeError("Integration runner must return RunResult")
        except Exception as provider_error:
            reported_error = _reported_error(provider_error)
            try:
                _call_with_retries(
                    "finish_failure",
                    lambda: client.integrations.finish(
                        integration_key,
                        run_id=run_id,
                        result=IntegrationResult.FAILURE,
                        changes_detected=None,
                        error=reported_error,
                    ),
                )
            except Exception as reporting_error:  # noqa: BLE001
                # The adapter failure remains the process's original error.
                logger.error(
                    "integration_failure_report_failed",
                    error_type=type(reporting_error).__name__,
                    status_code=_status_code(reporting_error),
                )
            logger.exception(
                "integration_run_failed",
                error_type=type(provider_error).__name__,
            )
            raise

        try:
            _call_with_retries(
                "finish_success",
                lambda: client.integrations.finish(
                    integration_key,
                    run_id=run_id,
                    result=IntegrationResult.SUCCESS,
                    changes_detected=result.changes_detected,
                    error=None,
                ),
            )
        except Exception as error:
            logger.error(
                "integration_success_report_failed",
                error_type=type(error).__name__,
                status_code=_status_code(error),
            )
            raise

        logger.info(
            "integration_run_finished",
            result="success",
            changes_detected=result.changes_detected,
        )
        return result
