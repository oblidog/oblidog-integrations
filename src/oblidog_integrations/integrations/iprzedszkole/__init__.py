"""iPrzedszkole integration."""

from .components import (
    ReceivablesComponentsSyncResult,
    sync_receivables_components,
)
from .models import Receivables
from .sync import run

__all__ = [
    "Receivables",
    "ReceivablesComponentsSyncResult",
    "run",
    "sync_receivables_components",
]
