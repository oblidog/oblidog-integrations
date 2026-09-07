"""JSON Schema generator for iPrzedszkole category data."""

from __future__ import annotations

import json
from typing import Any


def receivables_schema() -> dict[str, Any]:
    """Return the schema for the iPrzedszkole receivables snapshot."""
    labels = {
        "summary_to_pay": "Suma do zapłaty",
        "summary_paid": "Suma zapłacona",
        "summary_overdue": "Zaległość",
        "summary_overpayment": "Nadpłata",
        "costs_fixed": "Opłata stała",
        "costs_meal": "Wyżywienie",
        "costs_additional": "Opłaty dodatkowe",
    }
    properties = {
        key: {"type": "number", "title": label} for key, label in labels.items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def main() -> None:
    print(json.dumps(receivables_schema(), indent=2))


if __name__ == "__main__":
    main()
