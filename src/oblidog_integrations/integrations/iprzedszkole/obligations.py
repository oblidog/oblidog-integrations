"""Populate pending obligations from the current monthly receivables."""

from datetime import date, timedelta

import holidays
import structlog
from oblidog_client import OblidogClient, ObligationLifecycle, ObligationPeriod

from oblidog_integrations.integrations.iprzedszkole.models import Receivables

logger = structlog.get_logger(__name__)


def payment_due_date(on: date) -> date:
    """Return the month's tenth, moved back across weekends and Polish holidays."""
    due = date(on.year, on.month, 10)
    calendar = holidays.country_holidays("PL", years=on.year)
    while due.weekday() >= 5 or due in calendar:
        due -= timedelta(days=1)
    return due


def sync_receivables_obligation(
    *, oblidog: OblidogClient, receivables: Receivables, on: date
) -> bool:
    """Set a positive balance and mark draft/collecting obligations ready.

    Zero balances never erase historical amounts. Ready and terminal obligations
    are left untouched. The issue date is the day this run detects the charge;
    the provider does not expose its publication date.
    """
    if receivables.summary_to_pay <= 0:
        return False
    period = ObligationPeriod(on.year, on.month)
    obligation = oblidog.obligations.get(period)
    if obligation.lifecycle not in {
        ObligationLifecycle.DRAFT,
        ObligationLifecycle.COLLECTING_DATA,
    }:
        return False
    due_date = payment_due_date(on)
    oblidog.obligations.update(
        period,
        current_amount=str(receivables.summary_to_pay),
        issue_date=on,
        due_date=due_date,
    )
    oblidog.obligations.mark_ready(period)
    logger.info(
        "iprzedszkole_obligation_updated",
        obligation_key=obligation.key,
        previous_lifecycle=obligation.lifecycle.value,
        lifecycle=ObligationLifecycle.READY.value,
        current_amount=str(receivables.summary_to_pay),
        issue_date=on.isoformat(),
        due_date=due_date.isoformat(),
    )
    return True
