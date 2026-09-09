"""Publication of NJU Mobile account summaries as Oblidog category data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from oblidog_client import OblidogApiError, OblidogClient
from oblidog_client.generated.errors import UnexpectedStatus

from oblidog_integrations.integrations.nju.models import NjuAccountSummary

_GROSZ = Decimal("0.01")


@dataclass(frozen=True)
class AccountSummaryExportResult:
    """Outcome of comparing and exporting one NJU account summary."""

    summary: NjuAccountSummary
    created: bool


def account_summary_data(summary: NjuAccountSummary) -> dict[str, Any]:
    """Return the schema-stable, JSON-compatible account-summary snapshot."""
    return {
        "overpayment": _currency_amount(summary.overpayment),
        "amount_due": _currency_amount(summary.amount_due),
        "last_payment_amount": _currency_amount(summary.last_payment_amount),
        "billing_period_start": summary.billing_period_start.isoformat(),
        "billing_period_end": summary.billing_period_end.isoformat(),
        "liability_limit": _currency_amount(summary.liability_limit),
    }


def _currency_amount(amount: Decimal | None) -> float | None:
    """Convert a Polish-currency amount to a JSON number rounded to grosze."""
    return float(amount.quantize(_GROSZ)) if amount is not None else None


def _latest_data(
    oblidog: OblidogClient, category_code: str
) -> dict[str, object] | None:
    try:
        return oblidog.category_data.latest(category_code).data.to_dict()
    except (OblidogApiError, UnexpectedStatus) as error:
        if error.status_code == 404:
            return None
        raise


def export_account_summary(
    *,
    summary: NjuAccountSummary,
    oblidog: OblidogClient,
    category_code: str,
) -> AccountSummaryExportResult:
    """Create a category-data observation when the account summary changed."""
    data = account_summary_data(summary)
    if _latest_data(oblidog, category_code) == data:
        return AccountSummaryExportResult(summary=summary, created=False)

    oblidog.category_data.create(
        category_code,
        observed_at=datetime.now(UTC),
        data=data,
        source="nju",
    )
    return AccountSummaryExportResult(summary=summary, created=True)
