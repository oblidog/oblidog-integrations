"""JSON Schema generator for NJU Mobile account-summary category data."""

from __future__ import annotations

import json
from typing import Any


def account_summary_schema() -> dict[str, Any]:
    """Return the JSON Schema accepted by Oblidog Ledger for an NJU snapshot."""
    properties: dict[str, dict[str, Any]] = {
        "overpayment": {
            "anyOf": [{"type": "number"}, {"type": "null"}],
            "title": "Nadpłata",
        },
        "amount_due": {
            "anyOf": [{"type": "number"}, {"type": "null"}],
            "title": "Kwota do zapłaty",
        },
        "last_payment_amount": {
            "type": "number",
            "title": "Kwota ostatniej wpłaty",
        },
        "billing_period_start": {
            "type": "string",
            "format": "date",
            "title": "Początek okresu rozliczeniowego",
        },
        "billing_period_end": {
            "type": "string",
            "format": "date",
            "title": "Koniec okresu rozliczeniowego",
        },
        "liability_limit": {
            "type": "number",
            "title": "Limit należności",
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def main() -> None:
    """Print the schema for pasting into Oblidog Ledger."""
    print(json.dumps(account_summary_schema(), indent=2))


if __name__ == "__main__":
    main()
