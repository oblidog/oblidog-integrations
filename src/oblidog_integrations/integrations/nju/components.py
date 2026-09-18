"""Synchronization of NJU invoices as Oblidog obligation components."""

from __future__ import annotations

from dataclasses import dataclass

from oblidog_client import MutationResult, OblidogClient, ObligationPeriod

from oblidog_integrations.integrations.nju.models import NjuInvoice


@dataclass(frozen=True)
class InvoiceComponentsSyncResult:
    """Outcome of upserting invoices for one NJU obligation."""

    obligation_period: ObligationPeriod
    processed_count: int
    changed_count: int


def sync_invoice_components(
    *,
    oblidog: OblidogClient,
    obligation_period: ObligationPeriod,
    invoices: list[NjuInvoice],
) -> InvoiceComponentsSyncResult:
    """Upsert one invoice component for every invoice in an obligation period."""
    changed_count = 0
    for invoice in invoices:
        result = oblidog.obligations.upsert_component(
            obligation_period,
            type="invoice",
            label=invoice.document_number,
            amount=str(invoice.total_amount),
            external_id=invoice.document_number,
            metadata={
                "document_number": invoice.document_number,
                "issue_date": invoice.issue_date.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "paid_amount": str(invoice.paid_amount),
                "payable_amount": str(invoice.payable_amount),
                "accounting_period": invoice.accounting_period,
                "status": invoice.status,
                "paid": invoice.is_paid,
            },
        )
        if result.result is not MutationResult.UNCHANGED:
            changed_count += 1
    return InvoiceComponentsSyncResult(
        obligation_period=obligation_period,
        processed_count=len(invoices),
        changed_count=changed_count,
    )
