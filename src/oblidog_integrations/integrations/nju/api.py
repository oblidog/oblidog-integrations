"""Authenticated HTML client for the NJU Mobile invoice portal."""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from bs4 import BeautifulSoup

from oblidog_integrations.integrations.nju.models import NjuAccountSummary, NjuInvoice

LOGIN_URL = "https://www.njumobile.pl/logowanie?backUrl=/mojekonto/faktury"
POST_URL = "https://www.njumobile.pl/logowanie?_DARGS=/profile-processes/login/login.jsp.portal-login-form"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class NjuError(RuntimeError):
    """Raised when NJU Mobile cannot authenticate or provide invoice data."""


class NjuClient:
    """Fetch and parse invoices for one NJU Mobile account."""

    def __init__(
        self,
        *,
        phone: str,
        password: str,
        timeout: float = 30.0,
        opener_factory: Callable[..., Any] = build_opener,
    ) -> None:
        self._phone = phone
        self._password = password
        self._timeout = timeout
        self._opener_factory = opener_factory
        self.account_summary: NjuAccountSummary | None = None
        self.account_summary_error: str | None = None

    def fetch_invoices(self) -> list[NjuInvoice]:
        """Authenticate and return all invoices visible in the portal."""
        page = self._login_page()
        try:
            self.account_summary = parse_account_summary(page)
        except NjuError as error:
            # The summary is diagnostic data; a portal markup change must not
            # prevent invoice synchronization.
            self.account_summary_error = str(error)
        return parse_invoices(page)

    def _login_page(self) -> str:
        opener = self._opener_factory(HTTPCookieProcessor(CookieJar()))
        login_page = self._open(opener, Request(LOGIN_URL, headers=self._headers()))
        session_token = BeautifulSoup(login_page, "html.parser").find(
            "input", attrs={"name": "_dynSessConf"}
        )
        if session_token is None or not session_token.get("value"):
            raise NjuError("NJU Mobile login page did not provide a session token")

        payload = {
            "_dyncharset": "UTF-8",
            "_dynSessConf": session_token["value"],
            "/ptk/sun/login/formhandler/LoginFormHandler.backUrl": "/mojekonto/faktury",
            "_D:/ptk/sun/login/formhandler/LoginFormHandler.backUrl": "+",
            "/ptk/sun/login/formhandler/LoginFormHandler.hashMsisdn": "",
            "_D:/ptk/sun/login/formhandler/LoginFormHandler.hashMsisdn": "+",
            "phone-input": self._phone,
            "_D:phone-input": "+",
            "password-form": self._password,
            "_D:password-form": "+",
            "login-submit": "zaloguj+się",
            "_D:login-submit": "+",
            "_DARGS": "/profile-processes/login/login.jsp.portal-login-form",
        }
        request = Request(
            POST_URL,
            data=urlencode(payload).encode(),
            headers={
                **self._headers(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.njumobile.pl",
                "Referer": LOGIN_URL,
            },
        )
        invoices_page = self._open(opener, request)
        _require_authenticated_page(invoices_page)
        return invoices_page

    def _open(self, opener: Any, request: Request) -> str:
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, UnicodeDecodeError) as error:
            raise NjuError("NJU Mobile request failed") from error

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"User-Agent": USER_AGENT}


def parse_invoices(html: str) -> list[NjuInvoice]:
    """Parse invoice rows from an authenticated NJU Mobile invoices page."""
    soup = BeautifulSoup(html, "html.parser")
    invoices: list[NjuInvoice] = []
    for row in soup.select("tr[id^='id_abc-']"):
        if (
            "e-faktura będzie dostępna w ciągu"
            in row.get_text(" ", strip=True).casefold()
        ):
            continue
        fields = {cell.get("data-title"): cell for cell in row.select("td[data-title]")}
        if not fields:
            continue
        try:
            invoices.append(
                NjuInvoice(
                    document_number=_document_number(
                        fields["nr dokumentu"], fallback=str(row.get("id", ""))
                    ),
                    issue_date=_date(fields["data wystawienia"].get_text()),
                    due_date=_date(fields["termin płatności"].get_text()),
                    paid_amount=_amount(fields["kwota zapłacona"].get_text()),
                    payable_amount=_amount(fields["do zapłaty"].get_text()),
                    accounting_period=fields["za okres"].get_text(strip=True),
                    status=fields["status"].get_text(strip=True),
                )
            )
        except (KeyError, ValueError, InvalidOperation) as error:
            raise NjuError("NJU Mobile returned an invalid invoice row") from error
    return invoices


def parse_account_summary(html: str) -> NjuAccountSummary | None:
    """Parse the account summary displayed above the NJU invoice list."""
    summary = BeautifulSoup(html, "html.parser").select_one(
        "#expenses-list-content .s-dashboard-summary"
    )
    if summary is None:
        return None

    values: dict[str, str] = {}
    for row in summary.select(".row"):
        term = row.select_one(".term")
        if term is None:
            continue
        value = row.select_one(".definition") or row.select_one(
            ".eight.columns .six.columns"
        )
        if value is not None:
            values[_summary_label(term.get_text(" ", strip=True))] = value.get_text(
                " ", strip=True
            )

    try:
        last_payment_amount = values["kwota ostatniej wpłaty"]
        billing_period = values["okres rozliczeniowy"]
        liability_limit = values["limit należności"]
    except KeyError as error:
        available_fields = ", ".join(sorted(values)) or "none"
        raise NjuError(
            "NJU Mobile account summary is missing "
            f"{error.args[0]!r}; available fields: {available_fields}"
        ) from error

    period_dates = _billing_period_dates(billing_period)
    if period_dates is None:
        raise NjuError(
            "NJU Mobile account summary has an invalid billing period: "
            f"{billing_period!r}"
        )
    try:
        return NjuAccountSummary(
            overpayment=(_amount(values["nadpłata"]) if "nadpłata" in values else None),
            last_payment_amount=_amount(last_payment_amount),
            billing_period_start=period_dates[0],
            billing_period_end=period_dates[1],
            liability_limit=_amount(liability_limit),
            amount_due=(
                _amount(values["kwota do zapłaty"])
                if "kwota do zapłaty" in values
                else None
            ),
        )
    except (ValueError, InvalidOperation) as error:
        raise NjuError("NJU Mobile returned an invalid account summary") from error


def invoices_for_current_period(
    invoices: list[NjuInvoice], *, now: datetime
) -> list[NjuInvoice]:
    """Return invoices whose portal period matches the supplied month."""
    period = now.strftime("%m.%Y")
    return [invoice for invoice in invoices if invoice.accounting_period == period]


def _require_authenticated_page(html: str) -> None:
    """Reject a login response that is actually the portal login form again."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("input[name='phone-input'], input[name='password-form']"):
        raise NjuError("NJU Mobile rejected the configured credentials")


def _document_number(cell: Any, *, fallback: str) -> str:
    """Extract a non-empty, stable invoice identifier from a portal row."""
    if number := cell.get_text(" ", strip=True):
        return number

    document_input = cell.select_one(
        "input[name*='InvoiceDocumentRequestFormHandler.invoiceDocumentRequest']"
    )
    if document_input is not None and (
        number := document_input.get("title") or document_input.get("value")
    ):
        return str(number)

    anchor = cell.find("a")
    if anchor is not None:
        for attribute in ("id", "href"):
            if identifier := anchor.get(attribute):
                return str(identifier).rsplit("-", maxsplit=1)[-1]
    if fallback:
        return fallback.rsplit("-", maxsplit=1)[-1]
    raise ValueError("NJU Mobile invoice has no document identifier")


def _summary_label(value: str) -> str:
    """Normalize a label whose explanatory text may be nested inside it."""
    normalized = " ".join(value.split()).casefold()
    return re.split(r"\s+jeżeli\b", normalized, maxsplit=1)[0].rstrip(":")


def _date(value: str) -> date:
    day, month, year = value.strip().split(".")
    return date(int(year), int(month), int(day))


def _billing_period_dates(value: str) -> tuple[date, date] | None:
    numeric_dates = re.findall(r"\d{1,2}\D\d{1,2}\D\d{4}", value)
    if len(numeric_dates) == 2:
        return (
            _numeric_billing_date(numeric_dates[0]),
            _numeric_billing_date(numeric_dates[1]),
        )

    text_dates = re.findall(
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
        r"([A-Z][a-z]{2})\s+(\d{1,2})\s+\d{2}:\d{2}:\d{2}\s+\S+\s+(\d{4})",
        value,
    )
    if len(text_dates) == 2:
        return (
            _text_billing_date(*text_dates[0]),
            _text_billing_date(*text_dates[1]),
        )
    return None


def _numeric_billing_date(value: str) -> date:
    day, month, year = re.split(r"\D", value.strip())
    return date(int(year), int(month), int(day))


def _text_billing_date(month: str, day: str, year: str) -> date:
    try:
        month_number = list(calendar.month_abbr).index(month)
    except ValueError as error:
        raise ValueError(f"invalid month abbreviation: {month}") from error
    return date(int(year), month_number, int(day))


def _amount(value: str) -> Decimal:
    normalized = value.replace("\xa0", " ").strip().replace(",", ".")
    return Decimal(normalized.split()[0])
