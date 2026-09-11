from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import structlog
from oblidog_client import OblidogClient

from oblidog_integrations.integrations.ekartoteka.category_data import (
    export_snapshot,
)
from oblidog_integrations.integrations.ekartoteka.components import (
    sync_fee_components,
)
from oblidog_integrations.integrations.ekartoteka.ekartoteka import Ekartoteka
from oblidog_integrations.integrations.ekartoteka.obligations import (
    mark_error_when_current_fee_period_is_missing,
    populate_obligation_when_fee_period_is_available,
)
from oblidog_integrations.reporting import RunResult

logger = structlog.get_logger(__name__)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _component_periods(on: date) -> tuple[date, date]:
    """Return the previous and current billing periods to refresh.

    Args:
        on: Current calendar date in the integration's clock.

    Returns:
        First days of the previous and current obligation months.
    """
    previous_month = on.replace(day=1) - timedelta(days=1)
    return previous_month, on


def run() -> RunResult:
    """Synchronize e-Kartoteka snapshots, components, and obligations.

    Reads required credentials and target configuration from the environment.
    The run exports the annual category-data snapshot, refreshes fee components
    and obligation data for the previous and current months, then applies the
    missing-fee lifecycle check to the current month.

    Raises:
        RuntimeError: If required environment configuration is absent.
        EkartotekaError: If the provider returns invalid data.
    """
    now = datetime.now(UTC)
    logger.info("sync_started", integration="ekartoteka", year=now.year)

    ekartoteka_client = Ekartoteka(
        credentials={
            "username": _required_env("EKARTOTEKA_USERNAME"),
            "password": _required_env("EKARTOTEKA_PASSWORD"),
        }
    )
    ekartoteka_client.login()

    with (
        OblidogClient(
            base_url=_required_env("OBLIDOG_URL"),
            api_key=_required_env("OBLIDOG_API_KEY"),
        ) as client,
        client.integrations.run() as run,
    ):
        category_code = run.context["category"]["code"]
        snapshot_export = export_snapshot(
            ekartoteka=ekartoteka_client,
            oblidog=client,
            year=now.year,
        )
        billing_periods = _component_periods(now.date())
        components_syncs = [
            sync_fee_components(
                ekartoteka=ekartoteka_client,
                oblidog=client,
                category_code=category_code,
                on=period,
            )
            for period in billing_periods
        ]
        obligation_data_syncs = [
            populate_obligation_when_fee_period_is_available(
                ekartoteka=ekartoteka_client,
                oblidog=client,
                category_code=category_code,
                on=period,
            )
            for period in billing_periods
        ]
        obligation_check = mark_error_when_current_fee_period_is_missing(
            ekartoteka=ekartoteka_client,
            oblidog=client,
            category_code=category_code,
            on=now.date(),
        )
        known_change = (
            snapshot_export.created
            or any(result.updated for result in obligation_data_syncs)
            or obligation_check.marked_as_error
        )
        result = RunResult(
            changes_detected=(
                True
                if known_change
                else None
                if any(sync.upserted_count for sync in components_syncs)
                else False
            )
        )
        run.finish_success(changes_detected=result.changes_detected)
    if snapshot_export.created:
        logger.info(
            "snapshot_exported",
            category_code=category_code,
            year=now.year,
        )
    else:
        logger.info(
            "snapshot_export_skipped",
            category_code=category_code,
            reason="identical_latest_data",
            year=now.year,
        )
    for components_sync in components_syncs:
        logger.info(
            "fee_components_synced",
            obligation_key=components_sync.obligation_key,
            upserted_count=components_sync.upserted_count,
        )
    for obligation_data_sync in obligation_data_syncs:
        if obligation_data_sync.updated:
            logger.info(
                "obligation_populated_and_ready",
                obligation_key=obligation_data_sync.obligation_key,
                current_amount=str(obligation_data_sync.current_amount),
                issue_date=obligation_data_sync.issue_date.isoformat(),
                due_date=obligation_data_sync.due_date.isoformat(),
            )
        elif obligation_data_sync.fee_period_available:
            logger.info(
                "obligation_data_not_updated",
                obligation_key=obligation_data_sync.obligation_key,
                lifecycle=obligation_data_sync.lifecycle.value,
                reason="lifecycle_not_draft_or_collecting_data",
            )
        else:
            logger.info(
                "obligation_data_not_updated",
                reason="fee_period_unavailable",
            )
    if obligation_check.fee_period_available:
        logger.info("fee_period_available", year=now.year, month=now.month)
    elif obligation_check.marked_as_error:
        logger.warning(
            "obligation_marked_error",
            obligation_key=obligation_check.obligation_key,
            reason="fee_period_unavailable",
        )
    else:
        logger.info(
            "obligation_error_not_marked",
            obligation_key=obligation_check.obligation_key,
            lifecycle=obligation_check.lifecycle.value,
            reason="fee_period_unavailable",
        )
    return result
