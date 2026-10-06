from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from oblidog_client import ObligationLifecycle, ObligationPeriod

from oblidog_integrations.integrations.iprzedszkole.models import Receivables
from oblidog_integrations.integrations.iprzedszkole.obligations import (
    payment_due_date,
    sync_receivables_obligation,
)


def receivables(amount: str, paid: str = "0") -> Receivables:
    return Receivables(
        summary_to_pay=Decimal(amount),
        summary_paid=Decimal(paid),
        summary_overdue=Decimal(amount),
        summary_overpayment=Decimal(0),
        costs_fixed=None,
        costs_meal=Decimal(260),
        costs_additional=None,
    )


@pytest.mark.parametrize(
    "lifecycle",
    [
        ObligationLifecycle.DRAFT,
        ObligationLifecycle.COLLECTING_DATA,
    ],
)
def test_october_charge_populates_obligation_and_marks_ready(lifecycle):
    api = Mock()
    api.get.return_value = SimpleNamespace(key="PRSQ-2026-10", lifecycle=lifecycle)
    assert sync_receivables_obligation(
        oblidog=SimpleNamespace(obligations=api),
        receivables=receivables("260.00"),
        on=date(2026, 10, 3),
    )
    api.get.assert_called_once_with(ObligationPeriod(2026, 10))
    api.update.assert_called_once_with(
        ObligationPeriod(2026, 10),
        current_amount="260.00",
        issue_date=date(2026, 10, 3),
        due_date=date(2026, 10, 9),
    )
    api.mark_ready.assert_called_once_with(ObligationPeriod(2026, 10))
    assert [call[0] for call in api.mock_calls] == ["get", "update", "mark_ready"]


@pytest.mark.parametrize(
    "lifecycle",
    [
        value
        for value in ObligationLifecycle
        if value not in {ObligationLifecycle.DRAFT, ObligationLifecycle.COLLECTING_DATA}
    ],
)
def test_other_lifecycles_are_untouched_even_with_paid_positive_balance(lifecycle):
    api = Mock()
    api.get.return_value = SimpleNamespace(key="PRSQ-2026-09", lifecycle=lifecycle)
    assert not sync_receivables_obligation(
        oblidog=SimpleNamespace(obligations=api),
        receivables=receivables("287.20", "287.20"),
        on=date(2026, 9, 25),
    )
    api.update.assert_not_called()
    api.mark_ready.assert_not_called()


@pytest.mark.parametrize("amount", ["0", "-1"])
def test_nonpositive_balance_never_clears_history(amount):
    api = Mock()
    assert not sync_receivables_obligation(
        oblidog=SimpleNamespace(obligations=api),
        receivables=receivables(amount),
        on=date(2026, 10, 1),
    )
    assert api.mock_calls == []


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        (date(2026, 10, 3), date(2026, 10, 9)),
        (date(2026, 5, 1), date(2026, 5, 8)),
        (date(2026, 9, 7), date(2026, 9, 10)),
        (date(2023, 4, 1), date(2023, 4, 7)),  # Easter Monday on the tenth
    ],
)
def test_due_date_moves_back_over_weekends_and_polish_holidays(on, expected):
    assert payment_due_date(on) == expected


def test_update_failure_does_not_mark_ready():
    api = Mock()
    api.get.return_value = SimpleNamespace(
        key="PRSQ-2026-10",
        lifecycle=ObligationLifecycle.DRAFT,
    )
    api.update.side_effect = RuntimeError("update failed")
    with pytest.raises(RuntimeError, match="update failed"):
        sync_receivables_obligation(
            oblidog=SimpleNamespace(obligations=api),
            receivables=receivables("260"),
            on=date(2026, 10, 3),
        )
    api.mark_ready.assert_not_called()
