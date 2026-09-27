"""Synchronization of iPrzedszkole fee categories as obligation components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oblidog_client import MutationResult, OblidogClient, ObligationPeriod

from oblidog_integrations.integrations.iprzedszkole.models import Receivables


@dataclass(frozen=True)
class ReceivablesComponentsSyncResult:
    """Outcome of writing iPrzedszkole components for one obligation."""

    obligation_period: ObligationPeriod
    processed_count: int
    changed_count: int


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
    """Upsert only fees explicitly present in the portal's current response."""
    obligation_period = ObligationPeriod(on.year, on.month)
    changed_count = 0
    processed_count = 0
    for field, label in _COMPONENTS:
        amount = getattr(receivables, field)
        if amount is None:
            continue
        processed_count += 1
        result = oblidog.obligations.upsert_component(
            obligation_period,
            type="monthly_fee",
            label=label,
            amount=str(amount),
            external_id=field,
            metadata={"fee_kind": field},
        )
        if result.result is not MutationResult.UNCHANGED:
            changed_count += 1
    return ReceivablesComponentsSyncResult(
        obligation_period=obligation_period,
        processed_count=processed_count,
        changed_count=changed_count,
    )
