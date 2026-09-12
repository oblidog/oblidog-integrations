"""Synchronization of iPrzedszkole fee categories as obligation components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oblidog_client import OblidogClient, ObligationPeriod

from oblidog_integrations.integrations.iprzedszkole.models import Receivables


@dataclass(frozen=True)
class ReceivablesComponentsSyncResult:
    """Outcome of writing iPrzedszkole components for one obligation."""

    obligation_period: ObligationPeriod
    upserted_count: int


_COMPONENTS = (
    ("costs_fixed", "Opłata stała"),
    ("costs_meal", "Wyżywienie"),
    ("costs_additional", "Opłaty dodatkowe"),
)


def sync_receivables_components(
    *,
    oblidog: OblidogClient,
    receivables: Receivables,
    on: date,
) -> ReceivablesComponentsSyncResult:
    """Upsert the fixed, meal and additional fees for the current month."""
    obligation_period = ObligationPeriod(on.year, on.month)
    for field, label in _COMPONENTS:
        amount = getattr(receivables, field)
        oblidog.obligations.upsert_component(
            obligation_period,
            type="monthly_fee",
            label=label,
            amount=str(amount),
            external_id=field,
            metadata={"fee_kind": field},
        )
    return ReceivablesComponentsSyncResult(
        obligation_period=obligation_period,
        upserted_count=len(_COMPONENTS),
    )
