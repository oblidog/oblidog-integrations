"""Synchronize one iPrzedszkole account with Oblidog."""

from __future__ import annotations

import os
import re
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

logger = structlog.get_logger(__name__)


def _required_env(name: str) -> str:
    if value := os.getenv(name):
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def _category_code() -> str:
    """Return the iPrzedszkole category code, which is four letters long."""
    category_code = _required_env("OBLIDOG_CATEGORY_CODE")
    if not re.fullmatch(r"[A-Za-z]{4}", category_code):
        raise RuntimeError(
            "OBLIDOG_CATEGORY_CODE for iPrzedszkole must contain exactly four letters"
        )
    return category_code


def run() -> None:
    """Fetch one account's receivables and publish a changed snapshot."""
    now = datetime.now(ZoneInfo("Europe/Warsaw"))
    account_name = os.getenv("IPRZEDSZKOLE_ACCOUNT_NAME", "iprzedszkole")
    receivables = IprzedszkoleClient(
        kindergarten=_required_env("IPRZEDSZKOLE_KINDERGARTEN"),
        login=_required_env("IPRZEDSZKOLE_LOGIN"),
        password=_required_env("IPRZEDSZKOLE_PASSWORD"),
    ).fetch_receivables(on=now.date())
    category_code = _category_code()
    with OblidogClient(
        base_url=_required_env("OBLIDOG_URL"),
        api_key=_required_env("OBLIDOG_API_KEY"),
    ) as oblidog:
        created = export_receivables(
            receivables=receivables,
            oblidog=oblidog,
            category_code=category_code,
        )
        components_sync = sync_receivables_components(
            oblidog=oblidog,
            category_code=category_code,
            receivables=receivables,
            on=now.date(),
        )
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
        obligation_key=components_sync.obligation_key,
        upserted_count=components_sync.upserted_count,
    )
