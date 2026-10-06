from __future__ import annotations

import datetime
import os

import structlog
from oblidog_client import OblidogClient, ObligationPeriod

from oblidog_integrations.integrations.demo.provider import fetch
from oblidog_integrations.reporting import RunResult

logger = structlog.get_logger(__name__)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def run() -> RunResult:
    now = datetime.datetime.now(datetime.UTC)
    record = fetch()

    with (
        OblidogClient(
            base_url=_required_env("OBLIDOG_URL"),
            api_key=_required_env("OBLIDOG_API_KEY"),
        ) as client,
        client.integrations.run() as run,
    ):
        logger.info(
            "integration_run_started",
            integration="demo",
            category_code=run.context.category.code,
        )
        obligations = client.obligations.list(
            year=now.year,
            month=now.month,
        )

        if obligations.count != 1:
            raise RuntimeError(
                f"Expected exactly one matching obligation, got {obligations.count}"
            )

        period = ObligationPeriod(now.year, now.month)
        client.obligations.update(
            period,
            current_amount=str(record.amount),
        )
        client.obligations.append_note(
            period,
            f"Imported demo invoice {record.invoice_number}",
        )
        client.obligations.mark_ready(period)
        result = RunResult(changes_detected=True)
        run.finish_success(changes_detected=result.changes_detected)
        return result
