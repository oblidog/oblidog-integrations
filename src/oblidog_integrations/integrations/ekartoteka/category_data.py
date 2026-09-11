"""Publication of e-Kartoteka snapshots as Oblidog category data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from oblidog_client import OblidogApiError, OblidogClient
from oblidog_client.generated.errors import UnexpectedStatus

from oblidog_integrations.integrations.ekartoteka.ekartoteka import (
    Ekartoteka,
    SettlementSnapshot,
)


@dataclass(frozen=True)
class SnapshotExportResult:
    """Outcome of comparing and exporting one e-Kartoteka snapshot."""

    snapshot: SettlementSnapshot
    created: bool


def _latest_data(oblidog: OblidogClient) -> dict[str, object] | None:
    try:
        return oblidog.category_data.latest().data.to_dict()
    except (OblidogApiError, UnexpectedStatus) as error:
        if error.status_code == 404:
            return None
        raise


def export_snapshot(
    *,
    ekartoteka: Ekartoteka,
    oblidog: OblidogClient,
    year: int,
) -> SnapshotExportResult:
    """Create a category-data observation when the snapshot has changed.

    Args:
        ekartoteka: Authenticated provider facade used to fetch the snapshot.
        oblidog: Authenticated Oblidog client used to read and create data.
        year: Settlement year included in the snapshot.

    Returns:
        The generated snapshot and whether a new observation was created.

    Raises:
        OblidogApiError: If reading the latest category data fails for a
            reason other than a missing record.
    """
    snapshot = ekartoteka.get_settlement_snapshot(year)
    data = snapshot.model_dump(mode="json")
    if _latest_data(oblidog) == data:
        return SnapshotExportResult(snapshot=snapshot, created=False)

    oblidog.category_data.create(
        observed_at=datetime.now(UTC),
        data=data,
    )
    return SnapshotExportResult(snapshot=snapshot, created=True)
