"""Synchronize one iPrzedszkole account with Oblidog."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from oblidog_client import OblidogClient

from oblidog_integrations.integrations.iprzedszkole.api import IprzedszkoleClient
from oblidog_integrations.integrations.iprzedszkole.category_data import (
    export_receivables,
)
from oblidog_integrations.integrations.iprzedszkole.components import (
    sync_receivables_components,
)
from oblidog_integrations.reporting import RunResult

logger = structlog.get_logger(__name__)


def _required_env(name: str) -> str:
    if value := os.getenv(name):
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def run() -> RunResult:
    """Fetch one account's receivables and publish a changed snapshot."""
    now = datetime.now(ZoneInfo("Europe/Warsaw"))
    account_name = os.getenv("IPRZEDSZKOLE_ACCOUNT_NAME", "iprzedszkole")
    with (
        OblidogClient(
            base_url=_required_env("OBLIDOG_URL"),
            api_key=_required_env("OBLIDOG_API_KEY"),
        ) as oblidog,
        oblidog.integrations.run() as run,
    ):
        category_code = run.context.category.code
        receivables = IprzedszkoleClient(
            kindergarten=_required_env("IPRZEDSZKOLE_KINDERGARTEN"),
            login=_required_env("IPRZEDSZKOLE_LOGIN"),
            password=_required_env("IPRZEDSZKOLE_PASSWORD"),
        ).fetch_receivables(on=now.date())
        created = export_receivables(
            receivables=receivables,
            oblidog=oblidog,
        )
        components_sync = sync_receivables_components(
            oblidog=oblidog,
            receivables=receivables,
            on=now.date(),
        )
        result = RunResult(
            changes_detected=True
            if created
            else None
            if components_sync.upserted_count
            else False
        )
        run.finish_success(changes_detected=result.changes_detected)
    logger.info(
        "iprzedszkole_receivables_exported"
        if created
        else "iprzedszkole_receivables_export_skipped",
        account=account_name,
        category_code=category_code,
        reason=None if created else "identical_latest_data",
    )
    logger.info(
        "iprzedszkole_receivables_components_synced",
        account=account_name,
        obligation_period=str(components_sync.obligation_period),
        upserted_count=components_sync.upserted_count,
    )
    return result
