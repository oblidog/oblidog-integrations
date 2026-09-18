"""Synchronization of itemized e-Kartoteka fees as obligation components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oblidog_client import MutationResult, OblidogClient, ObligationPeriod

from oblidog_integrations.integrations.ekartoteka.ekartoteka import Ekartoteka


@dataclass(frozen=True)
class FeeComponentsSyncResult:
    """Outcome of upserting e-Kartoteka fees for one obligation."""

    obligation_period: ObligationPeriod
    processed_count: int
    changed_count: int


def sync_fee_components(
    *,
    ekartoteka: Ekartoteka,
    oblidog: OblidogClient,
    on: date,
) -> FeeComponentsSyncResult:
    """Upsert all itemized fees for an obligation month.

    Args:
        ekartoteka: Authenticated provider facade used to fetch fee items.
        oblidog: Authenticated Oblidog client used to upsert components.
        on: Month of the target obligation. The provider period is mapped from
            the preceding month.

    Returns:
        The target obligation period and component processing statistics.
    """
    obligation_period = ObligationPeriod(on.year, on.month)
    processed_count = 0
    changed_count = 0
    for component in ekartoteka.get_current_fee_components(on):
        for index, item in enumerate(component.items):
            result = oblidog.obligations.upsert_component(
                obligation_period,
                type="monthly_fee",
                label=item.name,
                amount=str(item.amount),
                external_id=(
                    f"{component.premises.id}:{component.period.charge_id}:{index}"
                ),
                metadata={
                    "premises": component.premises.model_dump(mode="json"),
                    "period": component.period.model_dump(mode="json"),
                    "fee": item.model_dump(mode="json"),
                },
            )
            processed_count += 1
            if result.result is not MutationResult.UNCHANGED:
                changed_count += 1
    return FeeComponentsSyncResult(
        obligation_period=obligation_period,
        processed_count=processed_count,
        changed_count=changed_count,
    )
