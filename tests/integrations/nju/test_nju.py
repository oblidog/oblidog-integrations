from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Self

import pytest
from oblidog_client import OblidogApiError, ObligationLifecycle
from oblidog_client.generated.errors import UnexpectedStatus

from oblidog_integrations.integrations.nju import sync
from oblidog_integrations.integrations.nju.api import (
    NjuError,
    _require_authenticated_page,
    invoices_for_current_period,
    parse_account_summary,
    parse_invoices,
)
from oblidog_integrations.integrations.nju.category_data import (
    account_summary_data,
    export_account_summary,
)
from oblidog_integrations.integrations.nju.components import sync_invoice_components
from oblidog_integrations.integrations.nju.models import NjuAccountSummary, NjuInvoice
from oblidog_integrations.integrations.nju.schema import account_summary_schema


def _fake_integrations() -> SimpleNamespace:
    return SimpleNamespace(
        run=lambda: nullcontext(
            SimpleNamespace(
                context={"category": {"code": "NJU"}},
                finish_success=lambda **_: None,
            )
        )
    )


def test_parse_invoices_extracts_the_portal_invoice_fields() -> None:
    invoices = parse_invoices(
        """
        <table>
          <tr id="id_abc-1">
            <td class="left-right-bg" data-title="nr dokumentu"><a id="doc-123">FV/2026/09/123</a></td>
            <td class="left-right-bg" data-title="data wystawienia">01.09.2026</td>
            <td class="left-right-bg" data-title="termin płatności">15.09.2026</td>
            <td class="left-right-bg" data-title="kwota zapłacona">12,34 PLN</td>
            <td class="left-right-bg" data-title="do zapłaty">0,00 PLN</td>
            <td class="left-right-bg" data-title="za okres">09.2026</td>
            <td class="left-right-bg" data-title="status">zapłacona</td>
          </tr>
        </table>
        """
    )

    assert invoices == [
        NjuInvoice(
            document_number="FV/2026/09/123",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 15),
            paid_amount=Decimal("12.34"),
            payable_amount=Decimal("0.00"),
            accounting_period="09.2026",
            status="zapłacona",
        )
    ]
    assert invoices[0].total_amount == Decimal("12.34")
    assert invoices[0].is_paid


def test_parse_invoices_falls_back_to_the_portal_identifier() -> None:
    invoices = parse_invoices(
        """
        <table>
          <tr id="id_abc-456">
            <td data-title="nr dokumentu"><a id="doc-123"></a></td>
            <td data-title="data wystawienia">01.09.2026</td>
            <td data-title="termin płatności">15.09.2026</td>
            <td data-title="kwota zapłacona">0,00 PLN</td>
            <td data-title="do zapłaty">12,34 PLN</td>
            <td data-title="za okres">09.2026</td>
            <td data-title="status">niezapłacona</td>
          </tr>
        </table>
        """
    )

    assert invoices[0].document_number == "123"


def test_parse_invoices_extracts_a_note_number_from_the_download_input() -> None:
    invoices = parse_invoices(
        """
        <table>
          <tr id="id_abc-3">
            <td data-title="nr dokumentu"><form><input name="/ptk/sun/ecare/invoices/form/InvoiceDocumentRequestFormHandler.invoiceDocumentRequest" title="26081057908875" value="26081057908875" type="submit"></form></td>
            <td data-title="data wystawienia">06.08.2026</td>
            <td data-title="termin płatności">20.08.2026</td>
            <td data-title="kwota zapłacona">0,04 zł</td>
            <td data-title="do zapłaty">0,00 zł</td>
            <td data-title="za okres">08.2026</td>
            <td data-title="status">zapłacona</td>
          </tr>
        </table>
        """
    )

    assert invoices[0].document_number == "26081057908875"


def test_parse_invoices_skips_the_pending_e_invoice_notice() -> None:
    invoices = parse_invoices(
        """
        <table>
          <tr id="id_abc-pending">
            <td data-title="nr dokumentu">e-faktura będzie dostępna w ciągu 42 godzin od wystawienia</td>
            <td data-title="data wystawienia"></td>
            <td data-title="termin płatności"></td>
            <td data-title="kwota zapłacona"></td>
            <td data-title="do zapłaty"></td>
            <td data-title="za okres"></td>
            <td data-title="status"></td>
          </tr>
        </table>
        """
    )

    assert invoices == []


def test_invoices_for_current_period_uses_the_portal_month_format() -> None:
    invoice = NjuInvoice(
        document_number="123",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        paid_amount=Decimal(0),
        payable_amount=Decimal("12.34"),
        accounting_period="09.2026",
        status="niezapłacona",
    )

    assert invoices_for_current_period(
        [invoice], now=datetime(2026, 9, 5, tzinfo=UTC)
    ) == [invoice]
    assert (
        invoices_for_current_period([invoice], now=datetime(2026, 10, 5, tzinfo=UTC))
        == []
    )


def test_parse_account_summary_extracts_balances_and_billing_period() -> None:
    summary = parse_account_summary(
        """
        <div id="expenses-list-content" class="s-payments-list">
          <div class="s-dashboard-summary">
            <div class="row"><div class="four columns mobile-six"><div class="term">nadpłata</div></div><div class="eight columns mobile-six"><div class="row"><div class="six columns"><div class="definition">0,60&nbsp;PLN</div></div></div></div></div>
            <div class="row"><div class="four columns mobile-six"><div class="term">kwota ostatniej wpłaty</div></div><div class="eight columns mobile-six"><div class="row"><div class="six columns"><div class="definition">59,04&nbsp;PLN</div></div></div></div></div>
            <div class="row"><div class="four columns mobile-six"><div class="term">okres rozliczeniowy</div></div><div class="eight columns mobile-six"><div class="row"><div class="six columns"><div class="definition">od&nbsp;06-08-2026&nbsp;do&nbsp;05-09-2026</div></div></div></div></div>
            <div class="row"><div class="four columns mobile-six"><div class="term">limit należności<br><span>Jeżeli Twoje wydatki przekroczą limit.</span></div></div><div class="eight columns mobile-six"><div class="row"><div class="six columns">100,00&nbsp;PLN</div></div></div></div>
          </div>
        </div>
        """
    )

    assert summary == NjuAccountSummary(
        overpayment=Decimal("0.60"),
        last_payment_amount=Decimal("59.04"),
        billing_period_start=date(2026, 8, 6),
        billing_period_end=date(2026, 9, 5),
        liability_limit=Decimal("100.00"),
    )


def test_parse_account_summary_accepts_other_date_separators() -> None:
    summary = parse_account_summary(
        """
        <div id="expenses-list-content"><div class="s-dashboard-summary">
          <div class="row"><div class="term">nadpłata:</div><div class="definition">0,60 PLN</div></div>
          <div class="row"><div class="term">kwota ostatniej wpłaty:</div><div class="definition">59,04 PLN</div></div>
          <div class="row"><div class="term">okres rozliczeniowy:</div><div class="definition">06.08.2026 – 05.09.2026</div></div>
          <div class="row"><div class="term">limit należności:</div><div class="definition">100,00 PLN</div></div>
        </div></div>
        """
    )

    assert summary is not None
    assert summary.billing_period_start == date(2026, 8, 6)
    assert summary.billing_period_end == date(2026, 9, 5)


def test_parse_account_summary_allows_a_missing_overpayment() -> None:
    summary = parse_account_summary(
        """
        <div id="expenses-list-content"><div class="s-dashboard-summary">
          <div class="row"><div class="term">kwota do zapłaty</div><div class="definition">0,00 PLN</div></div>
          <div class="row"><div class="term">kwota ostatniej wpłaty</div><div class="definition">59,04 PLN</div></div>
          <div class="row"><div class="term">okres rozliczeniowy</div><div class="definition">06.08.2026 – 05.09.2026</div></div>
          <div class="row"><div class="term">limit należności</div><div class="definition">100,00 PLN</div></div>
        </div></div>
        """
    )

    assert summary is not None
    assert summary.overpayment is None
    assert summary.amount_due == Decimal("0.00")
    assert summary.last_payment_amount == Decimal("59.04")


def test_parse_account_summary_accepts_javascript_rendered_dates() -> None:
    summary = parse_account_summary(
        """
        <div id="expenses-list-content"><div class="s-dashboard-summary">
          <div class="row"><div class="term">nadpłata</div><div class="definition">0,60 PLN</div></div>
          <div class="row"><div class="term">kwota ostatniej wpłaty</div><div class="definition">59,04 PLN</div></div>
          <div class="row"><div class="term">okres rozliczeniowy</div><div class="definition">od Thu Aug 06 00:00:00 CEST 2026 do Sat Sep 05 00:00:00 CEST 2026</div></div>
          <div class="row"><div class="term">limit należności</div><div class="definition">100,00 PLN</div></div>
        </div></div>
        """
    )

    assert summary is not None
    assert summary.billing_period_start == date(2026, 8, 6)
    assert summary.billing_period_end == date(2026, 9, 5)


def test_account_summary_schema_matches_the_exported_snapshot() -> None:
    schema = account_summary_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    assert schema["properties"]["overpayment"]["anyOf"] == [
        {"type": "number"},
        {"type": "null"},
    ]
    assert schema["properties"]["amount_due"]["anyOf"] == [
        {"type": "number"},
        {"type": "null"},
    ]
    assert schema["properties"]["billing_period_start"] == {
        "type": "string",
        "format": "date",
        "title": "Początek okresu rozliczeniowego",
    }


def test_account_summary_export_uses_a_flat_json_compatible_snapshot() -> None:
    summary = NjuAccountSummary(
        overpayment=Decimal("0.60"),
        last_payment_amount=Decimal("59.04"),
        billing_period_start=date(2026, 8, 6),
        billing_period_end=date(2026, 9, 5),
        liability_limit=Decimal("100.00"),
    )

    assert account_summary_data(summary) == {
        "overpayment": 0.6,
        "amount_due": None,
        "last_payment_amount": 59.04,
        "billing_period_start": "2026-08-06",
        "billing_period_end": "2026-09-05",
        "liability_limit": 100.0,
    }


def test_account_summary_export_includes_the_amount_due() -> None:
    summary = NjuAccountSummary(
        overpayment=None,
        amount_due=Decimal("12.34"),
        last_payment_amount=Decimal("59.04"),
        billing_period_start=date(2026, 8, 6),
        billing_period_end=date(2026, 9, 5),
        liability_limit=Decimal("100.00"),
    )

    assert account_summary_data(summary)["amount_due"] == 12.34
    assert account_summary_data(summary)["overpayment"] is None


def test_account_summary_export_rounds_currency_to_grosze() -> None:
    summary = NjuAccountSummary(
        overpayment=None,
        last_payment_amount=Decimal("0.6000000000000014"),
        billing_period_start=date(2026, 8, 6),
        billing_period_end=date(2026, 9, 5),
        liability_limit=Decimal("100.00"),
    )

    assert account_summary_data(summary)["last_payment_amount"] == 0.6


def test_account_summary_identical_to_latest_category_data_is_not_exported() -> None:
    summary = NjuAccountSummary(
        overpayment=None,
        last_payment_amount=Decimal("59.04"),
        billing_period_start=date(2026, 8, 6),
        billing_period_end=date(2026, 9, 5),
        liability_limit=Decimal("100.00"),
    )
    created: list[dict[str, object]] = []
    category_data = SimpleNamespace(
        latest=lambda: SimpleNamespace(
            data=SimpleNamespace(to_dict=lambda: account_summary_data(summary))
        ),
        create=lambda **kwargs: created.append(kwargs),
    )

    result = export_account_summary(
        summary=summary,
        oblidog=SimpleNamespace(category_data=category_data),
    )

    assert result.summary == summary
    assert not result.created
    assert not created


def test_account_summary_is_exported_when_sdk_reports_missing_data_as_404() -> None:
    summary = NjuAccountSummary(
        overpayment=None,
        last_payment_amount=Decimal("59.04"),
        billing_period_start=date(2026, 8, 6),
        billing_period_end=date(2026, 9, 5),
        liability_limit=Decimal("100.00"),
    )
    created: list[dict[str, object]] = []
    category_data = SimpleNamespace(
        latest=lambda: (_ for _ in ()).throw(
            UnexpectedStatus(404, b'{"detail":"Category data record not found"}')
        ),
        create=lambda **kwargs: created.append(kwargs),
    )

    result = export_account_summary(
        summary=summary,
        oblidog=SimpleNamespace(category_data=category_data),
    )

    assert result.created
    assert created[0]["data"] == account_summary_data(summary)


def test_run_exports_account_summary_without_invoices(monkeypatch) -> None:
    summary = NjuAccountSummary(
        overpayment=Decimal("0.60"),
        last_payment_amount=Decimal("59.04"),
        billing_period_start=date(2026, 8, 6),
        billing_period_end=date(2026, 9, 5),
        liability_limit=Decimal("100.00"),
    )
    captured: dict[str, object] = {}

    class FakeNjuClient:
        account_summary = summary
        account_summary_error = None

        def __init__(self, **_: object) -> None:
            pass

        def fetch_invoices(self) -> list[NjuInvoice]:
            return []

    class FakeOblidogClient:
        def __init__(self, **_: object) -> None:
            self.category_data = self
            self.integrations = _fake_integrations()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def latest(self) -> object:
            raise OblidogApiError(404)

        def create(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(sync, "NjuClient", FakeNjuClient)
    monkeypatch.setattr(sync, "OblidogClient", FakeOblidogClient)
    monkeypatch.setenv("NJU_PHONE", "phone")
    monkeypatch.setenv("NJU_PASSWORD", "password")
    monkeypatch.setenv("OBLIDOG_URL", "https://oblidog.example.com")
    monkeypatch.setenv("OBLIDOG_API_KEY", "api-key")
    result = sync.run()
    assert result.changes_detected is True

    assert captured["data"] == account_summary_data(summary)
    assert isinstance(captured["observed_at"], datetime)


def test_log_account_summary_includes_all_parsed_values(monkeypatch) -> None:
    log_entries: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        sync,
        "logger",
        SimpleNamespace(
            info=lambda event, **fields: log_entries.append((event, fields))
        ),
    )

    sync._log_account_summary(
        account="primary",
        summary=NjuAccountSummary(
            overpayment=Decimal("0.60"),
            last_payment_amount=Decimal("59.04"),
            billing_period_start=date(2026, 8, 6),
            billing_period_end=date(2026, 9, 5),
            liability_limit=Decimal("100.00"),
        ),
    )

    assert log_entries == [
        (
            "nju_account_summary",
            {
                "account": "primary",
                "overpayment": "0.60",
                "amount_due": None,
                "last_payment_amount": "59.04",
                "billing_period_start": "2026-08-06",
                "billing_period_end": "2026-09-05",
                "liability_limit": "100.00",
            },
        )
    ]


def test_log_recent_invoices_falls_back_to_the_previous_month(monkeypatch) -> None:
    log_entries: list[tuple[str, dict[str, object]]] = []
    invoice = NjuInvoice(
        document_number="previous-123",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 15),
        paid_amount=Decimal("5.00"),
        payable_amount=Decimal("7.34"),
        accounting_period="08.2026",
        status="niezapłacona",
    )

    monkeypatch.setattr(
        sync,
        "logger",
        SimpleNamespace(
            info=lambda event, **fields: log_entries.append((event, fields))
        ),
    )

    sync._log_recent_invoices(
        account="primary",
        invoices=[invoice],
        now=datetime(2026, 9, 5, tzinfo=UTC),
    )

    assert log_entries == [
        (
            "nju_invoices_fetched",
            {
                "account": "primary",
                "period": "08.2026",
                "period_source": "previous",
                "invoice_count": 1,
                "invoices": [
                    {
                        "invoice_number": "previous-123",
                        "issue_date": "2026-08-01",
                        "due_date": "2026-08-15",
                        "total_amount": "12.34",
                        "paid_amount": "5.00",
                        "payable_amount": "7.34",
                        "status": "niezapłacona",
                        "paid": False,
                    }
                ],
            },
        )
    ]


def test_log_recent_invoices_prefers_the_current_month(monkeypatch) -> None:
    log_entries: list[tuple[str, dict[str, object]]] = []
    current_invoice = NjuInvoice(
        document_number="current-123",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        paid_amount=Decimal("12.34"),
        payable_amount=Decimal("0.00"),
        accounting_period="09.2026",
        status="zapłacona",
    )
    previous_invoice = NjuInvoice(
        document_number="previous-123",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 15),
        paid_amount=Decimal("12.34"),
        payable_amount=Decimal("0.00"),
        accounting_period="08.2026",
        status="zapłacona",
    )
    monkeypatch.setattr(
        sync,
        "logger",
        SimpleNamespace(
            info=lambda event, **fields: log_entries.append((event, fields))
        ),
    )

    sync._log_recent_invoices(
        account="primary",
        invoices=[previous_invoice, current_invoice],
        now=datetime(2026, 9, 5, tzinfo=UTC),
    )

    _, fields = log_entries[0]
    assert fields["period"] == "09.2026"
    assert fields["period_source"] == "current"
    assert fields["invoices"] == [
        {
            "invoice_number": "current-123",
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": "12.34",
            "paid_amount": "12.34",
            "payable_amount": "0.00",
            "status": "zapłacona",
            "paid": True,
        }
    ]


def test_invoice_components_are_upserted_with_invoice_metadata() -> None:
    upserts: list[dict[str, object]] = []
    oblidog = SimpleNamespace(
        obligations=SimpleNamespace(
            upsert_component=lambda obligation_key, **kwargs: upserts.append(
                {"obligation_key": obligation_key, **kwargs}
            )
        )
    )
    invoice = NjuInvoice(
        document_number="FV/2026/09/123",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        paid_amount=Decimal("5.00"),
        payable_amount=Decimal("7.34"),
        accounting_period="09.2026",
        status="niezapłacona",
    )

    result = sync_invoice_components(
        oblidog=oblidog,
        obligation_key="NJU-2026-09",
        invoices=[invoice],
    )

    assert result.obligation_key == "NJU-2026-09"
    assert result.upserted_count == 1
    assert upserts == [
        {
            "obligation_key": "NJU-2026-09",
            "type": "invoice",
            "label": "FV/2026/09/123",
            "amount": "12.34",
            "external_id": "FV/2026/09/123",
            "metadata": {
                "document_number": "FV/2026/09/123",
                "issue_date": "2026-09-01",
                "due_date": "2026-09-15",
                "paid_amount": "5.00",
                "payable_amount": "7.34",
                "accounting_period": "09.2026",
                "status": "niezapłacona",
                "paid": False,
            },
        }
    ]


def test_run_upserts_components_for_the_previous_invoice_period(monkeypatch) -> None:
    now = datetime.now(UTC)
    previous_month = now.date().replace(day=1) - timedelta(days=1)
    invoice = NjuInvoice(
        document_number="FV/previous",
        issue_date=previous_month.replace(day=1),
        due_date=previous_month.replace(day=15),
        paid_amount=Decimal("12.34"),
        payable_amount=Decimal("0.00"),
        accounting_period=previous_month.strftime("%m.%Y"),
        status="zapłacona",
    )
    upserts: list[dict[str, object]] = []
    obligations = SimpleNamespace(
        list=lambda **_: pytest.fail("current obligation should not be fetched"),
        upsert_component=lambda obligation_key, **kwargs: upserts.append(
            {"obligation_key": obligation_key, **kwargs}
        ),
    )

    class FakeNjuClient:
        def __init__(self, **_: object) -> None:
            pass

        def fetch_invoices(self) -> list[NjuInvoice]:
            return [invoice]

    class FakeOblidogClient:
        def __init__(self, **_: object) -> None:
            self.obligations = obligations
            self.integrations = _fake_integrations()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(sync, "NjuClient", FakeNjuClient)
    monkeypatch.setattr(sync, "OblidogClient", FakeOblidogClient)
    monkeypatch.setenv("NJU_PHONE", "phone")
    monkeypatch.setenv("NJU_PASSWORD", "password")
    monkeypatch.setenv("OBLIDOG_URL", "https://oblidog.example.com")
    monkeypatch.setenv("OBLIDOG_API_KEY", "api-key")
    result = sync.run()
    assert result.changes_detected is None

    assert upserts[0]["obligation_key"] == (
        f"NJU-{previous_month.year}-{previous_month.month:02d}"
    )
    assert upserts[0]["external_id"] == "FV/previous"


def test_run_updates_and_readies_an_unpaid_current_invoice(monkeypatch) -> None:
    now = datetime.now(UTC)
    invoice = NjuInvoice(
        document_number="123",
        issue_date=now.date(),
        due_date=date(now.year, now.month, 15),
        paid_amount=Decimal("5.00"),
        payable_amount=Decimal("7.34"),
        accounting_period=now.strftime("%m.%Y"),
        status="niezapłacona",
    )
    updates: list[dict[str, object]] = []
    marked_ready: list[str] = []
    marked_paid: list[str] = []
    obligations = SimpleNamespace(
        list=lambda **_: SimpleNamespace(
            count=1,
            data=[
                SimpleNamespace(
                    key="NJU-2026-09",
                    lifecycle=ObligationLifecycle.COLLECTING_DATA,
                    current_amount=None,
                    issue_date=None,
                    due_date=None,
                )
            ],
        ),
        update=lambda key, **kwargs: updates.append({"key": key, **kwargs}),
        mark_ready=marked_ready.append,
        mark_paid=marked_paid.append,
        reopen=lambda _: None,
        upsert_component=lambda *_args, **_kwargs: None,
    )

    class FakeNjuClient:
        def __init__(self, **_: object) -> None:
            pass

        def fetch_invoices(self) -> list[NjuInvoice]:
            return [invoice]

    class FakeOblidogClient:
        def __init__(self, **_: object) -> None:
            self.obligations = obligations
            self.integrations = _fake_integrations()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(sync, "NjuClient", FakeNjuClient)
    monkeypatch.setattr(sync, "OblidogClient", FakeOblidogClient)
    monkeypatch.setenv("NJU_PHONE", "phone")
    monkeypatch.setenv("NJU_PASSWORD", "password")
    monkeypatch.setenv("OBLIDOG_URL", "https://oblidog.example.com")
    monkeypatch.setenv("OBLIDOG_API_KEY", "api-key")
    result = sync.run()
    assert result.changes_detected is True

    assert updates == [
        {
            "key": "NJU-2026-09",
            "current_amount": "12.34",
            "issue_date": now.date(),
            "due_date": date(now.year, now.month, 15),
        }
    ]
    assert marked_ready == ["NJU-2026-09"]
    assert not marked_paid


def test_run_marks_a_fully_paid_current_invoice_as_paid(monkeypatch) -> None:
    now = datetime.now(UTC)
    invoice = NjuInvoice(
        document_number="123",
        issue_date=now.date(),
        due_date=date(now.year, now.month, 15),
        paid_amount=Decimal("12.34"),
        payable_amount=Decimal(0),
        accounting_period=now.strftime("%m.%Y"),
        status="zapłacona",
    )
    marked_paid: list[str] = []
    obligations = SimpleNamespace(
        list=lambda **_: SimpleNamespace(
            count=1,
            data=[
                SimpleNamespace(
                    key="NJU-2026-09",
                    lifecycle=ObligationLifecycle.COLLECTING_DATA,
                    current_amount=None,
                    issue_date=None,
                    due_date=None,
                )
            ],
        ),
        update=lambda *_args, **_kwargs: None,
        mark_ready=lambda _: None,
        mark_paid=marked_paid.append,
        reopen=lambda _: None,
        upsert_component=lambda *_args, **_kwargs: None,
    )

    class FakeNjuClient:
        def __init__(self, **_: object) -> None:
            pass

        def fetch_invoices(self) -> list[NjuInvoice]:
            return [invoice]

    class FakeOblidogClient:
        def __init__(self, **_: object) -> None:
            self.obligations = obligations
            self.integrations = _fake_integrations()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(sync, "NjuClient", FakeNjuClient)
    monkeypatch.setattr(sync, "OblidogClient", FakeOblidogClient)
    monkeypatch.setenv("NJU_PHONE", "phone")
    monkeypatch.setenv("NJU_PASSWORD", "password")
    monkeypatch.setenv("OBLIDOG_URL", "https://oblidog.example.com")
    monkeypatch.setenv("OBLIDOG_API_KEY", "api-key")
    result = sync.run()
    assert result.changes_detected is True

    assert marked_paid == ["NJU-2026-09"]


def test_login_form_response_is_rejected_after_authentication() -> None:
    with pytest.raises(NjuError, match="rejected"):
        _require_authenticated_page(
            '<form><input name="phone-input"><input name="password-form"></form>'
        )


def test_reconcile_obligation_reopens_only_when_closed_data_or_status_changed() -> None:
    calls: list[str] = []
    obligation = SimpleNamespace(
        key="NJU-2026-09",
        lifecycle=ObligationLifecycle.READY,
        current_amount="12.34",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
    )

    class LifecycleEnforcingObligations:
        def reopen(self, key: str) -> None:
            assert obligation.lifecycle in {
                ObligationLifecycle.READY,
                ObligationLifecycle.PAID,
            }
            calls.append(f"reopen:{key}")
            obligation.lifecycle = ObligationLifecycle.COLLECTING_DATA

        def update(self, key: str, **_: object) -> None:
            assert obligation.lifecycle is ObligationLifecycle.COLLECTING_DATA
            calls.append(f"update:{key}")

        def mark_ready(self, key: str) -> None:
            assert obligation.lifecycle in {
                ObligationLifecycle.DRAFT,
                ObligationLifecycle.COLLECTING_DATA,
            }
            calls.append(f"ready:{key}")
            obligation.lifecycle = ObligationLifecycle.READY

        def mark_paid(self, key: str) -> None:
            assert obligation.lifecycle is ObligationLifecycle.READY
            calls.append(f"paid:{key}")
            obligation.lifecycle = ObligationLifecycle.PAID

    obligations = LifecycleEnforcingObligations()

    unchanged = sync._reconcile_obligation(
        obligations=obligations,
        obligation=obligation,
        total=Decimal("12.34"),
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        paid=False,
    )

    assert not unchanged
    assert not calls

    obligation.current_amount = "15.00"
    changed = sync._reconcile_obligation(
        obligations=obligations,
        obligation=obligation,
        total=Decimal("12.34"),
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        paid=True,
    )

    assert changed
    assert calls == [
        "reopen:NJU-2026-09",
        "update:NJU-2026-09",
        "ready:NJU-2026-09",
        "paid:NJU-2026-09",
    ]


def test_reconcile_obligation_marks_ready_before_paid_from_collecting_data() -> None:
    calls: list[str] = []
    obligation = SimpleNamespace(
        key="NJU-2026-09",
        lifecycle=ObligationLifecycle.COLLECTING_DATA,
        current_amount=None,
        issue_date=None,
        due_date=None,
    )

    class LifecycleEnforcingObligations:
        def update(self, key: str, **_: object) -> None:
            assert obligation.lifecycle is ObligationLifecycle.COLLECTING_DATA
            calls.append(f"update:{key}")

        def mark_ready(self, key: str) -> None:
            assert obligation.lifecycle is ObligationLifecycle.COLLECTING_DATA
            calls.append(f"ready:{key}")
            obligation.lifecycle = ObligationLifecycle.READY

        def mark_paid(self, key: str) -> None:
            assert obligation.lifecycle is ObligationLifecycle.READY
            calls.append(f"paid:{key}")
            obligation.lifecycle = ObligationLifecycle.PAID

    changed = sync._reconcile_obligation(
        obligations=LifecycleEnforcingObligations(),
        obligation=obligation,
        total=Decimal("12.34"),
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        paid=True,
    )

    assert changed
    assert calls == [
        "update:NJU-2026-09",
        "ready:NJU-2026-09",
        "paid:NJU-2026-09",
    ]


def test_reconcile_obligation_updates_issue_date_before_ready_from_draft() -> None:
    calls: list[str] = []
    obligation = SimpleNamespace(
        key="NJU-2026-09",
        lifecycle=ObligationLifecycle.DRAFT,
        current_amount="12.34",
        issue_date=None,
        due_date=date(2026, 9, 15),
    )

    class LifecycleEnforcingObligations:
        def update(self, key: str, **kwargs: object) -> None:
            assert obligation.lifecycle is ObligationLifecycle.DRAFT
            assert kwargs == {
                "current_amount": "12.34",
                "issue_date": date(2026, 9, 1),
                "due_date": date(2026, 9, 15),
            }
            calls.append(f"update:{key}")
            obligation.lifecycle = ObligationLifecycle.COLLECTING_DATA

        def mark_ready(self, key: str) -> None:
            assert obligation.lifecycle is ObligationLifecycle.COLLECTING_DATA
            calls.append(f"ready:{key}")
            obligation.lifecycle = ObligationLifecycle.READY

        def mark_paid(self, _: str) -> None:
            pytest.fail("An unpaid invoice must not be marked paid")

    changed = sync._reconcile_obligation(
        obligations=LifecycleEnforcingObligations(),
        obligation=obligation,
        total=Decimal("12.34"),
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        paid=False,
    )

    assert changed
    assert calls == ["update:NJU-2026-09", "ready:NJU-2026-09"]
