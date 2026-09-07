from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Self

import pytest
from oblidog_client import OblidogApiError, ObligationLifecycle
from oblidog_client.generated.errors import UnexpectedStatus

from oblidog_integrations.integrations.ekartoteka import sync
from oblidog_integrations.integrations.ekartoteka.api import EkartotekaApi
from oblidog_integrations.integrations.ekartoteka.category_data import (
    export_snapshot,
)
from oblidog_integrations.integrations.ekartoteka.components import (
    sync_fee_components,
)
from oblidog_integrations.integrations.ekartoteka.ekartoteka import (
    Ekartoteka,
    IncompleteSettlementDataError,
)
from oblidog_integrations.integrations.ekartoteka.models import (
    AnnualLedgerEntry,
    FeePeriodsPage,
    MonthlyFeeItemsPage,
    PremisesPage,
    SettlementAccount,
    SettlementAccountsPage,
    UpdateDatesPage,
)
from oblidog_integrations.integrations.ekartoteka.obligations import (
    mark_error_when_current_fee_period_is_missing,
    populate_obligation_when_fee_period_is_available,
)
from oblidog_integrations.integrations.ekartoteka.schema import (
    settlement_snapshot_schema,
)


class FakeEkartotekaApi(EkartotekaApi):
    def _request_json(
        self, url: str, *, payload: dict[str, str] | None = None
    ) -> object:
        if url == self.URL_TOKEN:
            return {"token": "header.eyJ1c2VybmFtZSI6IjQyX2FjY291bnQifQ.signature"}
        if url == self.URL_ME:
            return {
                "Nazwa": "Jan Kowalski",
                "Email": "jan@example.com",
                "DaneKsiegowe": [7],
            }
        return {
            "count": 0,
            "next": None,
            "previous": None,
            "results": [],
        }


class FakeEkartoteka(Ekartoteka):
    def __init__(self, *, delta: Decimal, last_update: datetime | None) -> None:
        super().__init__({})
        self.delta = delta
        self.last_update = last_update

    def get_current_fees_sum(self) -> Decimal:
        return Decimal("123.45")

    def get_settlements_sum(self, year: int) -> Decimal:
        return self.delta

    def get_update_stamp(self) -> dict[str, datetime]:
        return {} if self.last_update is None else {"LI": self.last_update}


class FakeComponentsApi:
    def get_premises(self) -> PremisesPage:
        return PremisesPage.model_validate(
            {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"IdLok": 10, "kod": "A-10", "adres": "Testowa 10"}],
            }
        )

    def get_fee_periods(self, premises_id: int) -> FeePeriodsPage:
        assert premises_id == 10
        return FeePeriodsPage.model_validate(
            {
                "count": 2,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "DataOd": "2026-09-01",
                        "DataDo": None,
                        "Stan": "ok",
                        "IdNal": 20,
                        "IdLok": 10,
                        "Naglowek": None,
                        "Stopka": None,
                        "Typ": "B",
                    },
                    {
                        "DataOd": "2026-08-01",
                        "DataDo": "2026-08-31",
                        "Stan": "ok",
                        "IdNal": 19,
                        "IdLok": 10,
                        "Naglowek": None,
                        "Stopka": None,
                        "Typ": "B",
                    },
                ],
            }
        )

    def get_monthly_fee_items(
        self, *, charge_id: int, premises_id: int
    ) -> MonthlyFeeItemsPage:
        assert (charge_id, premises_id) == (20, 10)
        return MonthlyFeeItemsPage.model_validate(
            {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "WspIle": 1,
                        "WspIleJM": None,
                        "Cena": 10,
                        "WspCena": 1,
                        "Nalicz": 10,
                        "is_sub": 0,
                        "Nazwa": "Czynsz",
                        "Ilosc": 1,
                        "JM": "mc",
                        "zaOkres": "",
                    }
                ],
            }
        )

    def get_settlements(self, year: int) -> SettlementAccountsPage:
        return FakeCompleteSnapshotApi().get_settlements(year)

    def get_annual_ledger(self, account_id: int) -> list[object]:
        return FakeCompleteSnapshotApi().get_annual_ledger(account_id)


class FakeSnapshotApi:
    def get_settlements(self, year: int) -> SettlementAccountsPage:
        assert year == 2026
        return SettlementAccountsPage.model_validate(
            {
                "count": 2,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "IdKon": 1,
                        "Rok": 2026,
                        "Symbol": "204",
                        "Nazwa": "Rozrachunki z właścicielami",
                        "Ma": 10,
                        "Wn": 20,
                        "s": 10,
                    },
                    {
                        "IdKon": 2,
                        "Rok": 2026,
                        "Symbol": "206",
                        "Nazwa": "Fundusz remontowy",
                        "Ma": 30,
                        "Wn": 40,
                        "s": 10,
                    },
                ],
            }
        )

    def get_update_dates(self) -> UpdateDatesPage:
        return UpdateDatesPage.model_validate(
            {
                "count": 2,
                "next": None,
                "previous": None,
                "results": [
                    {"typ": "LI", "data": "2026-09-01T08:20:03.316000"},
                    {"typ": "UNUSED", "data": None},
                ],
            }
        )

    def get_annual_ledger(self, account_id: int) -> list[object]:
        annual_ledger = {
            1: [
                {
                    "DoZaplaty": 100,
                    "Zaplacono": 0,
                    "Mc": 9,
                    "Nadplata": 0,
                    "Zaleglosc": 0,
                    "islimitMonths": 0,
                    "s": 0,
                }
            ],
            2: [
                {
                    "DoZaplaty": 20,
                    "Zaplacono": 0,
                    "Mc": 9,
                    "Nadplata": 0,
                    "Zaleglosc": 0,
                    "islimitMonths": 0,
                    "s": 0,
                }
            ],
        }
        return [
            AnnualLedgerEntry.model_validate(item) for item in annual_ledger[account_id]
        ]


class FakeCompleteSnapshotApi(FakeSnapshotApi):
    """Settlement fixture containing every account required for obligations."""

    def get_settlements(self, year: int) -> SettlementAccountsPage:
        page = super().get_settlements(year)
        return page.model_copy(
            update={
                "count": 3,
                "results": [
                    *page.results,
                    SettlementAccount.model_validate(
                        {
                            "IdKon": 3,
                            "Rok": 2026,
                            "Symbol": "210",
                            "Nazwa": "Odsetki",
                            "Ma": 0,
                            "Wn": 0,
                            "s": 0,
                        }
                    ),
                ],
            }
        )

    def get_annual_ledger(self, account_id: int) -> list[object]:
        if account_id == 3:
            return [
                AnnualLedgerEntry.model_validate(
                    {
                        "DoZaplaty": 0,
                        "Zaplacono": 0,
                        "Mc": 9,
                        "Nadplata": 0,
                        "Zaleglosc": 0,
                        "islimitMonths": 0,
                        "s": 0,
                    }
                )
            ]
        return super().get_annual_ledger(account_id)


def test_payment_status_marks_positive_balance_as_unpaid() -> None:
    client = FakeEkartoteka(
        delta=Decimal("10.0"),
        last_update=datetime.now(UTC),
    )

    status = client.get_payment_status()

    assert status.apartment_fee == Decimal("123.45")
    assert status.delta == Decimal("10.0")
    assert not status.paid
    assert status.force_unpaid == (datetime.now(UTC).day >= 25)


def test_payment_status_forces_unpaid_when_monthly_data_is_missing() -> None:
    client = FakeEkartoteka(delta=Decimal("-10.0"), last_update=None)

    status = client.get_payment_status()

    assert not status.paid
    assert status.force_unpaid


def test_settlement_response_has_pythonic_pydantic_fields() -> None:
    page = SettlementAccountsPage.model_validate(
        {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "IdKon": 1540355,
                    "Rok": 2026,
                    "Symbol": "204",
                    "Nazwa": "Rozrachunki z właścicielami",
                    "Ma": 4803.6,
                    "Wn": 4803.6,
                    "s": 0.0,
                }
            ],
        }
    )

    account = page.results[0]
    assert account.id == 1540355
    assert account.credit == Decimal("4803.6")
    assert account.model_dump(by_alias=True)["Nazwa"] == account.name


def test_obligation_amount_comes_from_monthly_settlement_ledgers() -> None:
    client = Ekartoteka(api=FakeCompleteSnapshotApi())  # type: ignore[arg-type]

    amount = client.get_obligation_amount_from_settlements(date(2026, 10, 3))

    assert amount == Decimal(120)


def test_obligation_amount_requires_every_settlement_account() -> None:
    client = Ekartoteka(api=FakeSnapshotApi())  # type: ignore[arg-type]

    with pytest.raises(IncompleteSettlementDataError, match="210"):
        client.get_obligation_amount_from_settlements(date(2026, 10, 3))


def test_obligation_amount_requires_the_target_month_in_every_ledger() -> None:
    class MissingMonthApi(FakeCompleteSnapshotApi):
        def get_annual_ledger(self, account_id: int) -> list[object]:
            return [] if account_id == 3 else super().get_annual_ledger(account_id)

    client = Ekartoteka(api=MissingMonthApi())  # type: ignore[arg-type]

    with pytest.raises(IncompleteSettlementDataError, match="account 210"):
        client.get_obligation_amount_from_settlements(date(2026, 10, 3))


def test_incomplete_settlements_do_not_update_or_ready_an_obligation() -> None:
    class IncompleteComponentsApi(FakeComponentsApi):
        def get_settlements(self, year: int) -> SettlementAccountsPage:
            return FakeSnapshotApi().get_settlements(year)

        def get_annual_ledger(self, account_id: int) -> list[object]:
            return FakeSnapshotApi().get_annual_ledger(account_id)

    updates: list[dict[str, object]] = []
    marked_ready: list[str] = []
    oblidog = SimpleNamespace(
        obligations=SimpleNamespace(
            get=lambda key: SimpleNamespace(
                key=key, lifecycle=ObligationLifecycle.COLLECTING_DATA
            ),
            update=lambda key, **kwargs: updates.append(
                {"obligation_key": key, **kwargs}
            ),
            mark_ready=marked_ready.append,
        )
    )

    with pytest.raises(IncompleteSettlementDataError):
        populate_obligation_when_fee_period_is_available(
            ekartoteka=Ekartoteka(api=IncompleteComponentsApi()),  # type: ignore[arg-type]
            oblidog=oblidog,
            category_code="FLAT",
            on=date(2026, 10, 3),
        )

    assert not updates
    assert not marked_ready


def test_fee_models_parse_dates_and_monetary_items() -> None:
    periods = FeePeriodsPage.model_validate(
        {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "DataOd": "2026-07-01",
                    "DataDo": None,
                    "Stan": "ok",
                    "IdNal": 1250689,
                    "IdLok": 918019,
                    "Naglowek": None,
                    "Stopka": None,
                    "Typ": "B",
                }
            ],
        }
    )
    items = MonthlyFeeItemsPage.model_validate(
        {
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "WspIle": 1.0,
                    "WspIleJM": None,
                    "Cena": 0.64,
                    "WspCena": 1.0,
                    "Nalicz": 31.42,
                    "is_sub": 0,
                    "Nazwa": "Ciepło (opłata stała)",
                    "Ilosc": 49.1,
                    "JM": "m2",
                    "zaOkres": "",
                }
            ],
        }
    )

    assert periods.results[0].starts_on.isoformat() == "2026-07-01"
    assert items.results[0].amount == Decimal("31.42")


def test_low_level_client_returns_pydantic_models() -> None:
    api = FakeEkartotekaApi({})

    user = api.login()
    settlements = api.get_settlements(2026)

    assert user.accounting_id == 7
    assert settlements == SettlementAccountsPage(count=0, results=[])


def test_low_level_client_fetches_every_page() -> None:
    class PaginatedApi(FakeEkartotekaApi):
        def _request_json(
            self, url: str, *, payload: dict[str, str] | None = None
        ) -> object:
            if url == "https://e-kartoteka.test/premises/2":
                return {
                    "count": 2,
                    "next": None,
                    "previous": "https://e-kartoteka.test/premises/1",
                    "results": [{"IdLok": 11, "kod": "A-11", "adres": "Testowa 11"}],
                }
            return {
                "count": 2,
                "next": "https://e-kartoteka.test/premises/2",
                "previous": None,
                "results": [{"IdLok": 10, "kod": "A-10", "adres": "Testowa 10"}],
            }

    api = PaginatedApi({})
    api._logged_in = True
    api.token = "token"
    api.client_id = 42
    api.user = SimpleNamespace(accounting_id=7)  # type: ignore[assignment]

    premises = api.get_premises()

    assert premises.count == 2
    assert premises.next is None
    assert [premises.id for premises in premises.results] == [10, 11]


def test_update_date_model_accepts_timestamps_and_missing_dates() -> None:
    page = UpdateDatesPage.model_validate(
        {
            "count": 2,
            "next": None,
            "previous": None,
            "results": [
                {"typ": "LI", "data": "2026-09-01T08:20:03.316000"},
                {"typ": "NRB", "data": None},
            ],
        }
    )

    assert page.results[0].updated_at.isoformat() == "2026-09-01T08:20:03.316000"
    assert page.results[1].updated_at is None


def test_current_fee_components_uses_the_active_period() -> None:
    client = Ekartoteka(api=FakeComponentsApi())  # type: ignore[arg-type]

    components = client.get_current_fee_components(date(2026, 10, 3))

    assert len(components) == 1
    assert components[0].premises.code == "A-10"
    assert components[0].period.charge_id == 20
    assert components[0].items[0].name == "Czynsz"


def test_current_fee_period_uses_the_preceding_fee_period() -> None:
    client = Ekartoteka(api=FakeComponentsApi())  # type: ignore[arg-type]

    assert client.has_current_fee_period(date(2026, 10, 3))
    assert client.has_current_fee_period(date(2026, 9, 3))
    assert not client.has_current_fee_period(date(2026, 8, 3))


def test_settlement_snapshot_has_a_stable_flat_shape() -> None:
    client = Ekartoteka(api=FakeSnapshotApi())  # type: ignore[arg-type]

    snapshot = client.get_settlement_snapshot(2026)

    assert snapshot.year == 2026
    assert snapshot.account_204_credit == 10.0
    assert snapshot.account_206_debit == 40.0
    assert snapshot.account_210_balance is None
    assert snapshot.update_li_at.isoformat() == "2026-09-01T08:20:03.316000+02:00"
    assert snapshot.update_dk_at is None
    assert set(snapshot.model_dump()) == {
        "year",
        "account_204_credit",
        "account_204_debit",
        "account_204_balance",
        "account_206_credit",
        "account_206_debit",
        "account_206_balance",
        "account_210_credit",
        "account_210_debit",
        "account_210_balance",
        "update_dk_at",
        "update_dkl_at",
        "update_li_at",
        "update_nrb_at",
        "update_nl_at",
    }


def test_settlement_snapshot_schema_matches_the_flat_snapshot() -> None:
    schema = settlement_snapshot_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    assert schema["properties"]["account_204_balance"]["anyOf"] == [
        {"type": "number"},
        {"type": "null"},
    ]
    assert (
        schema["properties"]["account_204_balance"]["title"]
        == "Rozrachunki z właścicielami — Saldo"
    )
    assert schema["properties"]["update_li_at"]["anyOf"] == [
        {"type": "string", "format": "date-time"},
        {"type": "null"},
    ]


def test_run_exports_the_snapshot_as_category_data(monkeypatch) -> None:
    snapshot = Ekartoteka(api=FakeSnapshotApi()).get_settlement_snapshot(2026)  # type: ignore[arg-type]
    captured: dict[str, object] = {}

    class FakeSyncEkartoteka:
        def __init__(self, **_: object) -> None:
            pass

        def login(self) -> None:
            pass

        def get_settlement_snapshot(self, year: int):
            assert year == datetime.now(UTC).year
            return snapshot

        def has_current_fee_period(self, on: date) -> bool:
            assert on == datetime.now(UTC).date()
            return True

        def get_current_fee_components(self, on: date) -> list[object]:
            assert on in {
                datetime.now(UTC).date(),
                datetime.now(UTC).date().replace(day=1) - timedelta(days=1),
            }
            return []

    class FakeOblidogClient:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            self.category_data = self

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def create(self, category_code: str, **kwargs: object) -> None:
            captured["category_code"] = category_code
            captured.update(kwargs)

        def latest(self, _: str) -> object:
            raise OblidogApiError(404)

    monkeypatch.setattr(sync, "Ekartoteka", FakeSyncEkartoteka)
    monkeypatch.setattr(sync, "OblidogClient", FakeOblidogClient)
    monkeypatch.setenv("EKARTOTEKA_USERNAME", "user")
    monkeypatch.setenv("EKARTOTEKA_PASSWORD", "password")
    monkeypatch.setenv("OBLIDOG_URL", "https://oblidog.example.com")
    monkeypatch.setenv("OBLIDOG_API_KEY", "api-key")
    monkeypatch.setenv("OBLIDOG_CATEGORY_CODE", "FLAT")

    sync.run()

    assert captured["category_code"] == "FLAT"
    assert captured["source"] == "ekartoteka"
    assert captured["data"] == snapshot.model_dump(mode="json")
    assert isinstance(captured["observed_at"], datetime)


def test_fee_components_are_upserted_with_provider_metadata() -> None:
    upserts: list[dict[str, object]] = []
    oblidog = SimpleNamespace(
        obligations=SimpleNamespace(
            upsert_component=lambda obligation_key, **kwargs: upserts.append(
                {"obligation_key": obligation_key, **kwargs}
            )
        )
    )

    result = sync_fee_components(
        ekartoteka=Ekartoteka(api=FakeComponentsApi()),  # type: ignore[arg-type]
        oblidog=oblidog,
        category_code="FLAT",
        on=date(2026, 10, 3),
    )

    assert result.obligation_key == "FLAT-2026-10"
    assert result.upserted_count == 1
    assert upserts == [
        {
            "obligation_key": "FLAT-2026-10",
            "type": "monthly_fee",
            "label": "Czynsz",
            "amount": "10",
            "source": "ekartoteka",
            "external_id": "10:20:0",
            "metadata": {
                "premises": {"id": 10, "code": "A-10", "address": "Testowa 10"},
                "period": {
                    "starts_on": "2026-09-01",
                    "ends_on": None,
                    "status": "ok",
                    "charge_id": 20,
                    "premises_id": 10,
                    "header": None,
                    "footer": None,
                    "type": "B",
                },
                "fee": {
                    "quantity_factor": "1",
                    "quantity_factor_unit": None,
                    "unit_price": "10",
                    "price_factor": "1",
                    "amount": "10",
                    "is_subitem": False,
                    "name": "Czynsz",
                    "quantity": "1",
                    "unit": "mc",
                    "period_description": "",
                },
            },
        }
    ]


def test_published_fees_populate_and_ready_draft_or_collecting_obligation() -> None:
    for lifecycle in (
        ObligationLifecycle.DRAFT,
        ObligationLifecycle.COLLECTING_DATA,
    ):
        updates: list[dict[str, object]] = []
        marked_ready: list[str] = []
        obligations = SimpleNamespace(
            get=lambda key, lifecycle=lifecycle: SimpleNamespace(
                key=key, lifecycle=lifecycle
            ),
            update=lambda key, updates=updates, **kwargs: updates.append(
                {"obligation_key": key, **kwargs}
            ),
            mark_ready=marked_ready.append,
        )
        oblidog = SimpleNamespace(obligations=obligations)

        result = populate_obligation_when_fee_period_is_available(
            ekartoteka=Ekartoteka(api=FakeComponentsApi()),  # type: ignore[arg-type]
            oblidog=oblidog,
            category_code="FLAT",
            on=date(2026, 10, 3),
        )

        assert result.updated
        assert result.current_amount == Decimal(120)
        assert result.issue_date == date(2026, 9, 1)
        assert result.due_date == date(2026, 10, 15)
        assert updates == [
            {
                "obligation_key": "FLAT-2026-10",
                "current_amount": "120",
                "issue_date": date(2026, 9, 1),
                "due_date": date(2026, 10, 15),
            }
        ]
        assert marked_ready == ["FLAT-2026-10"]


def test_published_fees_do_not_overwrite_ready_obligation() -> None:
    updates: list[dict[str, object]] = []
    marked_ready: list[str] = []
    obligations = SimpleNamespace(
        get=lambda key: SimpleNamespace(key=key, lifecycle=ObligationLifecycle.READY),
        update=lambda key, **kwargs: updates.append({"obligation_key": key, **kwargs}),
        mark_ready=marked_ready.append,
    )
    oblidog = SimpleNamespace(obligations=obligations)

    result = populate_obligation_when_fee_period_is_available(
        ekartoteka=Ekartoteka(api=FakeComponentsApi()),  # type: ignore[arg-type]
        oblidog=oblidog,
        category_code="FLAT",
        on=date(2026, 10, 3),
    )

    assert result.fee_period_available
    assert not result.updated
    assert result.lifecycle is ObligationLifecycle.READY
    assert not updates
    assert not marked_ready


def test_snapshot_identical_to_latest_category_data_is_not_exported() -> None:
    snapshot = Ekartoteka(api=FakeSnapshotApi()).get_settlement_snapshot(2026)  # type: ignore[arg-type]
    created: list[dict[str, object]] = []
    category_data = SimpleNamespace(
        latest=lambda _: SimpleNamespace(
            data=SimpleNamespace(to_dict=lambda: snapshot.model_dump(mode="json"))
        ),
        create=lambda _, **kwargs: created.append(kwargs),
    )
    oblidog = SimpleNamespace(category_data=category_data)

    result = export_snapshot(
        ekartoteka=Ekartoteka(api=FakeSnapshotApi()),  # type: ignore[arg-type]
        oblidog=oblidog,
        category_code="FLAT",
        year=2026,
    )

    assert result.snapshot == snapshot
    assert not result.created
    assert not created


def test_snapshot_is_exported_when_sdk_reports_missing_data_as_404() -> None:
    created: list[dict[str, object]] = []
    category_data = SimpleNamespace(
        latest=lambda _: (_ for _ in ()).throw(
            UnexpectedStatus(404, b'{"detail":"Category data record not found"}')
        ),
        create=lambda _, **kwargs: created.append(kwargs),
    )
    oblidog = SimpleNamespace(category_data=category_data)

    result = export_snapshot(
        ekartoteka=Ekartoteka(api=FakeSnapshotApi()),  # type: ignore[arg-type]
        oblidog=oblidog,
        category_code="FLAT",
        year=2026,
    )

    assert result.created
    assert created[0]["source"] == "ekartoteka"


def test_snapshot_export_propagates_non_missing_oblidog_api_errors() -> None:
    class FailingCategoryData:
        def latest(self, _: str) -> object:
            raise OblidogApiError(500, b"upstream error")

    oblidog = SimpleNamespace(category_data=FailingCategoryData())

    with pytest.raises(OblidogApiError, match="HTTP 500"):
        export_snapshot(
            ekartoteka=Ekartoteka(api=FakeSnapshotApi()),  # type: ignore[arg-type]
            oblidog=oblidog,
            category_code="FLAT",
            year=2026,
        )


def test_missing_fee_period_marks_non_collecting_obligation_as_error() -> None:
    marked_as_error: list[str] = []
    obligations = SimpleNamespace(
        get=lambda key: SimpleNamespace(
            key=key,
            lifecycle=ObligationLifecycle.READY,
        ),
        mark_error=marked_as_error.append,
    )
    oblidog = SimpleNamespace(obligations=obligations)
    ekartoteka = SimpleNamespace(has_current_fee_period=lambda _: False)

    result = mark_error_when_current_fee_period_is_missing(
        ekartoteka=ekartoteka,
        oblidog=oblidog,
        category_code="FLAT",
        on=date(2026, 9, 3),
    )

    assert result.marked_as_error
    assert not result.fee_period_available
    assert result.lifecycle is ObligationLifecycle.READY
    assert marked_as_error == ["FLAT-2026-09"]


def test_missing_fee_period_keeps_collecting_obligation_unchanged() -> None:
    marked_as_error: list[str] = []
    obligations = SimpleNamespace(
        get=lambda key: SimpleNamespace(
            key=key,
            lifecycle=ObligationLifecycle.COLLECTING_DATA,
        ),
        mark_error=marked_as_error.append,
    )
    oblidog = SimpleNamespace(obligations=obligations)
    ekartoteka = SimpleNamespace(has_current_fee_period=lambda _: False)

    result = mark_error_when_current_fee_period_is_missing(
        ekartoteka=ekartoteka,
        oblidog=oblidog,
        category_code="FLAT",
        on=date(2026, 9, 3),
    )

    assert not result.marked_as_error
    assert not result.fee_period_available
    assert result.lifecycle is ObligationLifecycle.COLLECTING_DATA
    assert not marked_as_error
