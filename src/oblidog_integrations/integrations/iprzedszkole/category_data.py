"""Export iPrzedszkole receivables as Oblidog category data."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from oblidog_client import OblidogApiError, OblidogClient
from oblidog_client.generated.errors import UnexpectedStatus

from oblidog_integrations.integrations.iprzedszkole.models import Receivables


def receivables_data(receivables: Receivables) -> dict[str, float]:
    """Return a flat, JSON-compatible receivables snapshot."""
    return {
        name: float(value.quantize(Decimal("0.01")))
        for name, value in vars(receivables).items()
    }


def export_receivables(*, receivables: Receivables, oblidog: OblidogClient) -> bool:
    """Create an observation only when its data differs from the latest one."""
    data = receivables_data(receivables)
    try:
        latest = oblidog.category_data.latest().data.to_dict()
    except (OblidogApiError, UnexpectedStatus) as error:
        if error.status_code != 404:
            raise
        latest: dict[str, Any] | None = None
    if latest == data:
        return False
    oblidog.category_data.create(observed_at=datetime.now(UTC), data=data)
    return True
