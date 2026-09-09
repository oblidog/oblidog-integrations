from __future__ import annotations

import datetime
import os

from oblidog_client import OblidogClient

from oblidog_integrations.integrations.demo.provider import fetch
from oblidog_integrations.reporting import RunResult


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def run() -> RunResult:
    now = datetime.datetime.now(datetime.UTC)
    record = fetch()

    with OblidogClient(
        base_url=_required_env("OBLIDOG_URL"),
        api_key=_required_env("OBLIDOG_API_KEY"),
    ) as client:
        obligations = client.obligations.list(
            year=now.year,
            month=now.month,
            category_code=_required_env("OBLIDOG_CATEGORY_CODE"),
        )

        if obligations.count != 1:
            raise RuntimeError(
                f"Expected exactly one matching obligation, got {obligations.count}"
            )

        obligation = obligations.data[0]
        client.obligations.update(
            obligation.key,
            current_amount=str(record.amount),
        )
        client.obligations.append_note(
            obligation.key,
            f"Imported demo invoice {record.invoice_number}",
        )
        client.obligations.mark_ready(obligation.key)
    return RunResult(changes_detected=True)
