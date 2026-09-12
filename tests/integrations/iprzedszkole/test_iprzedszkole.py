from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from oblidog_client import ObligationPeriod

from oblidog_integrations.integrations.iprzedszkole.api import (
    aspnet_tokens,
    parse_receivables,
    school_year_start,
)
from oblidog_integrations.integrations.iprzedszkole.components import (
    sync_receivables_components,
)
from oblidog_integrations.integrations.iprzedszkole.models import Receivables


def test_aspnet_tokens_reads_present_hidden_fields() -> None:
    assert aspnet_tokens(
        '<input name="__VIEWSTATE" value="view"><input name="__EVENTVALIDATION" value="event">'
    ) == {
        "__VIEWSTATE": "view",
        "__EVENTVALIDATION": "event",
        "__VIEWSTATEGENERATOR": "",
    }


def test_school_year_start_uses_september_boundary() -> None:
    assert school_year_start(date(2026, 8, 31)) == 2025
    assert school_year_start(date(2026, 9, 1)) == 2026


def test_parse_receivables_uses_current_period_and_fee_kinds() -> None:
    receivables = parse_receivables(
        {
            "d": {
                "ListData": [
                    {
                        "Rok": 2026,
                        "Miesiac": 8,
                        "DoZaplaty": "99,00",
                        "Zaplacono": 1,
                        "Zaleglosc": 2,
                        "Nadplata": 3,
                    },
                    {
                        "Rok": 2026,
                        "Miesiac": 9,
                        "DoZaplaty": "12,34",
                        "Zaplacono": "5,00",
                        "Zaleglosc": "0",
                        "Nadplata": "0,60",
                    },
                ]
            }
        },
        {
            "d": {
                "ListK": [
                    {"RodzajOplaty": 0, "Kwota": "4,00"},
                    {"RodzajOplaty": 1, "Kwota": "2,34"},
                    {"RodzajOplaty": 2, "Kwota": "6,00"},
                ]
            }
        },
        on=date(2026, 9, 7),
    )

    assert receivables == Receivables(
        summary_to_pay=Decimal("12.34"),
        summary_paid=Decimal("5.00"),
        summary_overdue=Decimal(0),
        summary_overpayment=Decimal("0.60"),
        costs_fixed=Decimal("4.00"),
        costs_meal=Decimal("6.00"),
        costs_additional=Decimal("2.34"),
    )


def test_sync_receivables_components_upserts_three_stable_components() -> None:
    calls: list[tuple[ObligationPeriod, dict[str, object]]] = []

    class Obligations:
        def upsert_component(self, period: ObligationPeriod, **kwargs: object) -> None:
            calls.append((period, kwargs))

    result = sync_receivables_components(
        oblidog=SimpleNamespace(obligations=Obligations()),
        receivables=Receivables(
            summary_to_pay=Decimal("12.34"),
            summary_paid=Decimal(0),
            summary_overdue=Decimal(0),
            summary_overpayment=Decimal(0),
            costs_fixed=Decimal("4.00"),
            costs_meal=Decimal("6.00"),
            costs_additional=Decimal("2.34"),
        ),
        on=date(2026, 9, 7),
    )

    assert result.obligation_period == ObligationPeriod(2026, 9)
    assert result.upserted_count == 3
    assert calls == [
        (
            ObligationPeriod(2026, 9),
            {
                "type": "monthly_fee",
                "label": "Opłata stała",
                "amount": "4.00",
                "external_id": "costs_fixed",
                "metadata": {"fee_kind": "costs_fixed"},
            },
        ),
        (
            ObligationPeriod(2026, 9),
            {
                "type": "monthly_fee",
                "label": "Wyżywienie",
                "amount": "6.00",
                "external_id": "costs_meal",
                "metadata": {"fee_kind": "costs_meal"},
            },
        ),
        (
            ObligationPeriod(2026, 9),
            {
                "type": "monthly_fee",
                "label": "Opłaty dodatkowe",
                "amount": "2.34",
                "external_id": "costs_additional",
                "metadata": {"fee_kind": "costs_additional"},
            },
        ),
    ]
