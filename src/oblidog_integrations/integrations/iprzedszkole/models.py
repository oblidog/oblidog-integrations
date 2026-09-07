"""Provider models for iPrzedszkole receivables."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Receivables:
    """The monthly balance and fee breakdown shown in iPrzedszkole."""

    summary_to_pay: Decimal
    summary_paid: Decimal
    summary_overdue: Decimal
    summary_overpayment: Decimal
    costs_fixed: Decimal
    costs_meal: Decimal
    costs_additional: Decimal
