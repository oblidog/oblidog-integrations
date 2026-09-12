"""Synchronize one NJU Mobile account with one Oblidog category."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from oblidog_client import OblidogClient, ObligationLifecycle, ObligationPeriod

from oblidog_integrations.integrations.nju.api import (
    NjuClient,
    invoices_for_current_period,
)
from oblidog_integrations.integrations.nju.category_data import (
    export_account_summary,
)
from oblidog_integrations.integrations.nju.components import sync_invoice_components
from oblidog_integrations.integrations.nju.models import NjuAccountSummary, NjuInvoice
from oblidog_integrations.reporting import RunResult

logger = structlog.get_logger(__name__)

_EDITABLE_LIFECYCLES = {
    ObligationLifecycle.DRAFT,
    ObligationLifecycle.COLLECTING_DATA,
}
_REOPENABLE_LIFECYCLES = {
    ObligationLifecycle.READY,
    ObligationLifecycle.PAID,
}


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _previous_period(now: datetime) -> str:
    """Return the portal's period format for the month before ``now``."""
    previous_month = now.date().replace(day=1) - timedelta(days=1)
    return previous_month.strftime("%m.%Y")


def _invoices_for_period(
    invoices: list[NjuInvoice], *, period: str
) -> list[NjuInvoice]:
    """Return invoices belonging to one NJU portal accounting period."""
    return [invoice for invoice in invoices if invoice.accounting_period == period]


def _obligation_period_from_provider_period(period: str) -> ObligationPeriod:
    """Convert NJU's ``MM.YYYY`` period to an Oblidog obligation period."""
    month, year = period.split(".")
    return ObligationPeriod(year=int(year), month=int(month))


def _log_recent_invoices(
    *, account: str, invoices: list[NjuInvoice], now: datetime
) -> None:
    """Log current invoices, falling back to the previous portal period."""
    current_period = now.strftime("%m.%Y")
    selected_invoices = invoices_for_current_period(invoices, now=now)
    selected_period = current_period
    period_source = "current"
    if not selected_invoices:
        selected_period = _previous_period(now)
        selected_invoices = _invoices_for_period(invoices, period=selected_period)
        period_source = "previous" if selected_invoices else "none"

    logger.info(
        "nju_invoices_fetched",
        account=account,
        period=selected_period,
        period_source=period_source,
        invoice_count=len(selected_invoices),
        invoices=[
            {
                "invoice_number": invoice.document_number,
                "issue_date": invoice.issue_date.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "total_amount": str(invoice.total_amount),
                "paid_amount": str(invoice.paid_amount),
                "payable_amount": str(invoice.payable_amount),
                "status": invoice.status,
                "paid": invoice.is_paid,
            }
            for invoice in selected_invoices
        ],
    )


def _log_account_summary(*, account: str, summary: NjuAccountSummary) -> None:
    """Log balances and billing dates shown in the NJU account summary."""
    logger.info(
        "nju_account_summary",
        account=account,
        overpayment=(
            str(summary.overpayment) if summary.overpayment is not None else None
        ),
        amount_due=(
            str(summary.amount_due) if summary.amount_due is not None else None
        ),
        last_payment_amount=str(summary.last_payment_amount),
        billing_period_start=summary.billing_period_start.isoformat(),
        billing_period_end=summary.billing_period_end.isoformat(),
        liability_limit=str(summary.liability_limit),
    )


def _has_current_values(
    obligation: Any, *, total: Decimal, issue_date: date, due_date: date
) -> bool:
    try:
        current_amount = Decimal(str(obligation.current_amount))
    except (InvalidOperation, ValueError):
        return False
    return (
        current_amount == total
        and obligation.issue_date == issue_date
        and obligation.due_date == due_date
    )


def _reconcile_obligation(
    *,
    obligations: Any,
    obligation: Any,
    period: ObligationPeriod,
    total: Decimal,
    issue_date: date,
    due_date: date,
    paid: bool,
) -> bool:
    """Apply NJU data while respecting Oblidog's obligation lifecycle."""
    values_current = _has_current_values(
        obligation, total=total, issue_date=issue_date, due_date=due_date
    )
    lifecycle = obligation.lifecycle
    target_lifecycle = ObligationLifecycle.PAID if paid else ObligationLifecycle.READY

    if lifecycle in _REOPENABLE_LIFECYCLES:
        if values_current and lifecycle == target_lifecycle:
            return False
        obligations.reopen(period)
        if not values_current:
            obligations.update(
                period,
                current_amount=str(total),
                issue_date=issue_date,
                due_date=due_date,
            )
    elif lifecycle in _EDITABLE_LIFECYCLES:
        if not values_current:
            obligations.update(
                period,
                current_amount=str(total),
                issue_date=issue_date,
                due_date=due_date,
            )
    else:
        logger.warning(
            "nju_obligation_skipped",
            obligation_key=obligation.key,
            lifecycle=lifecycle.value,
            reason="lifecycle_not_editable_or_reopenable",
        )
        return False

    obligations.mark_ready(period)
    if paid:
        obligations.mark_paid(period)
    return True


def run() -> RunResult:
    """Synchronize the current NJU invoice period for one configured account."""
    now = datetime.now(ZoneInfo("Europe/Warsaw"))
    account_name = os.getenv("NJU_ACCOUNT_NAME", "nju")
    nju = NjuClient(
        phone=_required_env("NJU_PHONE"),
        password=_required_env("NJU_PASSWORD"),
    )
    all_invoices = nju.fetch_invoices()
    if summary := getattr(nju, "account_summary", None):
        _log_account_summary(account=account_name, summary=summary)
    elif summary_error := getattr(nju, "account_summary_error", None):
        logger.warning(
            "nju_account_summary_unavailable",
            account=account_name,
            reason=summary_error,
        )
    _log_recent_invoices(
        account=account_name,
        invoices=all_invoices,
        now=now,
    )
    invoices = invoices_for_current_period(all_invoices, now=now)
    previous_period = _previous_period(now)
    previous_invoices = _invoices_for_period(all_invoices, period=previous_period)
    summary_changed = False
    previous_components_upserted = 0
    with (
        OblidogClient(
            base_url=_required_env("OBLIDOG_URL"),
            api_key=_required_env("OBLIDOG_API_KEY"),
        ) as oblidog,
        oblidog.integrations.run() as run,
    ):
        category_code = run.context.category.code
        if summary := getattr(nju, "account_summary", None):
            summary_export = export_account_summary(
                summary=summary,
                oblidog=oblidog,
            )
            logger.info(
                "nju_account_summary_exported"
                if summary_export.created
                else "nju_account_summary_export_skipped",
                account=account_name,
                category_code=category_code,
                reason=None if summary_export.created else "identical_latest_data",
            )
            summary_changed = summary_export.created
        if not invoices and not previous_invoices:
            logger.info(
                "nju_invoices_absent",
                account=account_name,
                period=now.strftime("%m.%Y"),
            )
            result = RunResult(changes_detected=summary_changed)
            run.finish_success(changes_detected=result.changes_detected)
            return result
        if previous_invoices:
            previous_components_sync = sync_invoice_components(
                oblidog=oblidog,
                obligation_period=_obligation_period_from_provider_period(
                    previous_period
                ),
                invoices=previous_invoices,
            )
            logger.info(
                "nju_invoice_components_synced",
                account=account_name,
                obligation_period=str(previous_components_sync.obligation_period),
                upserted_count=previous_components_sync.upserted_count,
            )
            previous_components_upserted = previous_components_sync.upserted_count
        if not invoices:
            logger.info(
                "nju_invoices_absent",
                account=account_name,
                period=now.strftime("%m.%Y"),
            )
            result = RunResult(
                changes_detected=(
                    True
                    if summary_changed
                    else None
                    if previous_components_upserted
                    else False
                )
            )
            run.finish_success(changes_detected=result.changes_detected)
            return result
        obligations = oblidog.obligations.list(
            year=now.year,
            month=now.month,
        )
        if obligations.count != 1:
            raise RuntimeError(
                f"Expected exactly one NJU obligation for {category_code}, got "
                f"{obligations.count}"
            )
        obligation = obligations.data[0]
        obligation_period = ObligationPeriod(now.year, now.month)
        components_sync = sync_invoice_components(
            oblidog=oblidog,
            obligation_period=obligation_period,
            invoices=invoices,
        )
        total = sum((invoice.total_amount for invoice in invoices), start=0)
        issue_date = min(invoice.issue_date for invoice in invoices)
        due_date = min(invoice.due_date for invoice in invoices)
        paid = all(invoice.is_paid for invoice in invoices)
        changed = _reconcile_obligation(
            obligations=oblidog.obligations,
            obligation=obligation,
            period=obligation_period,
            total=total,
            issue_date=issue_date,
            due_date=due_date,
            paid=paid,
        )
        result = RunResult(
            changes_detected=(
                True
                if summary_changed or changed
                else None
                if previous_components_upserted or components_sync.upserted_count
                else False
            )
        )
        run.finish_success(changes_detected=result.changes_detected)

    logger.info(
        "nju_obligation_synced",
        account=account_name,
        obligation_key=obligation.key,
        invoice_count=len(invoices),
        current_amount=str(total),
        issue_date=issue_date.isoformat(),
        due_date=due_date.isoformat(),
        paid=paid,
        changed=changed,
    )
    logger.info(
        "nju_invoice_components_synced",
        account=account_name,
        obligation_period=str(components_sync.obligation_period),
        upserted_count=components_sync.upserted_count,
    )
    return result
