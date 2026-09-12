"""e-Kartoteka-driven lifecycle checks for Oblidog obligations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from oblidog_client import OblidogClient, ObligationLifecycle, ObligationPeriod

from oblidog_integrations.integrations.ekartoteka.ekartoteka import Ekartoteka

_LIFECYCLES_ALLOWED_WITHOUT_FEES = {
    ObligationLifecycle.DRAFT,
    ObligationLifecycle.COLLECTING_DATA,
}


@dataclass(frozen=True)
class ObligationFeePeriodCheck:
    """Result of checking source fees against one Oblidog obligation."""

    fee_period_available: bool
    obligation_key: str | None
    lifecycle: ObligationLifecycle | None
    marked_as_error: bool


@dataclass(frozen=True)
class ObligationFeeDataSyncResult:
    """Outcome of filling an obligation from a published fee period."""

    fee_period_available: bool
    obligation_key: str | None
    lifecycle: ObligationLifecycle | None
    updated: bool
    current_amount: Decimal | None
    issue_date: date | None
    due_date: date | None


def populate_obligation_when_fee_period_is_available(
    *,
    ekartoteka: Ekartoteka,
    oblidog: OblidogClient,
    on: date,
) -> ObligationFeeDataSyncResult:
    """Fill a draft/collecting obligation when e-Kartoteka publishes charges.

    e-Kartoteka exposes the charge date as the fee period's ``starts_on`` date,
    but does not expose a payment due date.  The latter therefore defaults to
    the 15th day of the obligation month.

    Args:
        ekartoteka: Authenticated provider facade used to fetch charges and
            settlement-ledger amounts.
        oblidog: Authenticated Oblidog client used to update the obligation.
        on: Month of the target obligation.

    Returns:
        Whether a fee period was available and whether obligation data was
        written and marked ready.
    """
    components = ekartoteka.get_current_fee_components(on)
    if not components:
        return ObligationFeeDataSyncResult(
            fee_period_available=False,
            obligation_key=None,
            lifecycle=None,
            updated=False,
            current_amount=None,
            issue_date=None,
            due_date=None,
        )

    period = ObligationPeriod(on.year, on.month)
    obligation = oblidog.obligations.get(period)
    if obligation.lifecycle not in _LIFECYCLES_ALLOWED_WITHOUT_FEES:
        return ObligationFeeDataSyncResult(
            fee_period_available=True,
            obligation_key=obligation.key,
            lifecycle=obligation.lifecycle,
            updated=False,
            current_amount=None,
            issue_date=None,
            due_date=None,
        )

    current_amount = ekartoteka.get_obligation_amount_from_settlements(on)
    issue_date = components[0].period.starts_on
    due_date = date(on.year, on.month, 15)
    oblidog.obligations.update(
        period,
        current_amount=str(current_amount),
        issue_date=issue_date,
        due_date=due_date,
    )
    oblidog.obligations.mark_ready(period)
    return ObligationFeeDataSyncResult(
        fee_period_available=True,
        obligation_key=obligation.key,
        lifecycle=obligation.lifecycle,
        updated=True,
        current_amount=current_amount,
        issue_date=issue_date,
        due_date=due_date,
    )


def mark_error_when_current_fee_period_is_missing(
    *,
    ekartoteka: Ekartoteka,
    oblidog: OblidogClient,
    on: date,
) -> ObligationFeePeriodCheck:
    """Mark the current obligation as erroneous when provider fees are absent.

    Draft and collecting-data obligations are intentionally left unchanged:
    their lifecycle already represents that the current source data is not
    complete yet.

    Args:
        ekartoteka: Authenticated provider facade used to check fee periods.
        oblidog: Authenticated Oblidog client used to read and mark obligations.
        on: Month of the obligation being checked.

    Returns:
        Whether fees exist and whether the obligation was marked as erroneous.
    """
    if ekartoteka.has_current_fee_period(on):
        return ObligationFeePeriodCheck(
            fee_period_available=True,
            obligation_key=None,
            lifecycle=None,
            marked_as_error=False,
        )

    period = ObligationPeriod(on.year, on.month)
    obligation = oblidog.obligations.get(period)
    if obligation.lifecycle in _LIFECYCLES_ALLOWED_WITHOUT_FEES:
        return ObligationFeePeriodCheck(
            fee_period_available=False,
            obligation_key=obligation.key,
            lifecycle=obligation.lifecycle,
            marked_as_error=False,
        )

    oblidog.obligations.mark_error(period)
    return ObligationFeePeriodCheck(
        fee_period_available=False,
        obligation_key=obligation.key,
        lifecycle=obligation.lifecycle,
        marked_as_error=True,
    )
