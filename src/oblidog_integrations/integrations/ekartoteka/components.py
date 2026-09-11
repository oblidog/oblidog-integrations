"""Synchronization of itemized e-Kartoteka fees as obligation components."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oblidog_client import OblidogClient

from oblidog_integrations.integrations.ekartoteka.ekartoteka import Ekartoteka


@dataclass(frozen=True)
class FeeComponentsSyncResult:
    """Outcome of upserting e-Kartoteka fees for one obligation."""

    obligation_key: str
    upserted_count: int


def sync_fee_components(
    *,
    ekartoteka: Ekartoteka,
    oblidog: OblidogClient,
    category_code: str,
    on: date,
) -> FeeComponentsSyncResult:
    """Upsert all itemized fees for an obligation month.

    Args:
        ekartoteka: Authenticated provider facade used to fetch fee items.
        oblidog: Authenticated Oblidog client used to upsert components.
        category_code: Prefix used to build the Oblidog obligation key.
        on: Month of the target obligation. The provider period is mapped from
            the preceding month.

    Returns:
        The target obligation key and number of upserted fee items.
    """
    obligation_key = f"{category_code}-{on.year:04d}-{on.month:02d}"
    upserted_count = 0
    for component in ekartoteka.get_current_fee_components(on):
        for index, item in enumerate(component.items):
            oblidog.obligations.upsert_component(
                obligation_key,
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
        obligation_key=obligation_key,
        upserted_count=upserted_count,
    )
