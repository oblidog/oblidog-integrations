"""Synchronization of itemized e-Kartoteka fees as obligation components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oblidog_client import OblidogClient, ObligationPeriod

from oblidog_integrations.integrations.ekartoteka.ekartoteka import Ekartoteka


@dataclass(frozen=True)
class FeeComponentsSyncResult:
    """Outcome of upserting e-Kartoteka fees for one obligation."""

    obligation_period: ObligationPeriod
    upserted_count: int


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
        The target obligation period and number of upserted fee items.
    """
    obligation_period = ObligationPeriod(on.year, on.month)
    upserted_count = 0
    for component in ekartoteka.get_current_fee_components(on):
        for index, item in enumerate(component.items):
            oblidog.obligations.upsert_component(
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
            upserted_count += 1
    return FeeComponentsSyncResult(
        obligation_period=obligation_period,
        upserted_count=upserted_count,
    )
