"""Validated provider models for NJU Mobile invoices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class NjuInvoice:
    """One invoice shown in the NJU Mobile customer portal."""

    document_number: str
    issue_date: date
    due_date: date
    paid_amount: Decimal
    payable_amount: Decimal
    accounting_period: str
    status: str

    @property
    def total_amount(self) -> Decimal:
        """Return the complete invoice amount, including a settled amount."""
        return self.paid_amount + self.payable_amount

    @property
    def is_paid(self) -> bool:
        """Whether the portal marks this invoice as paid."""
        return self.status.casefold() == "zapłacona"


@dataclass(frozen=True)
class NjuAccountSummary:
    """Account balances and billing dates shown above the invoice list."""

    # NJU displays either an overpayment or the amount currently due.
    overpayment: Decimal | None
    last_payment_amount: Decimal
    billing_period_start: date
    billing_period_end: date
    liability_limit: Decimal
    amount_due: Decimal | None = None
