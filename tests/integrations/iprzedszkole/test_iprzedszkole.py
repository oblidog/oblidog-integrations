from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from oblidog_integrations.integrations.iprzedszkole.api import (
    aspnet_tokens,
    parse_receivables,
    school_year_start,
)
from oblidog_integrations.integrations.iprzedszkole.components import (
    sync_receivables_components,
)
from oblidog_integrations.integrations.iprzedszkole.models import Receivables
from oblidog_integrations.integrations.iprzedszkole.sync import _category_code


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


def test_category_code_must_be_exactly_four_letters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBLIDOG_CATEGORY_CODE", "IPRZ")
    assert _category_code() == "IPRZ"

    monkeypatch.setenv("OBLIDOG_CATEGORY_CODE", "IPRZ1")
    with pytest.raises(RuntimeError, match="exactly four letters"):
        _category_code()


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
    calls: list[tuple[str, dict[str, object]]] = []

    class Obligations:
        def upsert_component(self, key: str, **kwargs: object) -> None:
            calls.append((key, kwargs))

    result = sync_receivables_components(
        oblidog=SimpleNamespace(obligations=Obligations()),
        category_code="KINDERGARTEN",
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

    assert result.obligation_key == "KINDERGARTEN-2026-09"
    assert result.upserted_count == 3
    assert calls == [
        (
            "KINDERGARTEN-2026-09",
            {
                "type": "monthly_fee",
                "label": "Opłata stała",
                "amount": "4.00",
                "source": "iprzedszkole",
                "external_id": "costs_fixed",
                "metadata": {"fee_kind": "costs_fixed"},
            },
        ),
        (
            "KINDERGARTEN-2026-09",
            {
                "type": "monthly_fee",
                "label": "Wyżywienie",
                "amount": "6.00",
                "source": "iprzedszkole",
                "external_id": "costs_meal",
                "metadata": {"fee_kind": "costs_meal"},
            },
        ),
        (
            "KINDERGARTEN-2026-09",
            {
                "type": "monthly_fee",
                "label": "Opłaty dodatkowe",
                "amount": "2.34",
                "source": "iprzedszkole",
                "external_id": "costs_additional",
                "metadata": {"fee_kind": "costs_additional"},
            },
        ),
    ]
