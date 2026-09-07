"""Authenticated client for the iPrzedszkole parent portal."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from bs4 import BeautifulSoup

from oblidog_integrations.integrations.iprzedszkole.models import Receivables

URL_BASE = "https://iprzedszkole.progman.pl"
URL_LOGIN = "/iprzedszkole/Authentication/login.aspx"
URL_MEAL_PLAN = "/iprzedszkole/Pages/PanelRodzica/Jadlospis/Jadlospis.aspx"
URL_RECEIVABLES = "/iprzedszkole/Pages/PanelRodzica/Naleznosci/Naleznosci.aspx"
URL_RECEIVABLES_ANNUAL = (
    "/iprzedszkole/Pages/PanelRodzica/Naleznosci/"
    "ws_Naleznosci.asmx/pobierzDaneRaportRoczny"
)
URL_RECEIVABLES_DATA = (
    "/iprzedszkole/Pages/PanelRodzica/Naleznosci/ws_Naleznosci.asmx/pobierzDaneOplat"
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class IprzedszkoleError(RuntimeError):
    """Raised when iPrzedszkole cannot authenticate or return valid data."""


def aspnet_tokens(html: str) -> dict[str, str]:
    """Extract the ASP.NET hidden fields required by the login form."""
    soup = BeautifulSoup(html, "html.parser")
    return {
        name: str(element.get("value", "")) if element is not None else ""
        for name in ("__VIEWSTATE", "__EVENTVALIDATION", "__VIEWSTATEGENERATOR")
        for element in [soup.find("input", attrs={"name": name})]
    }


def school_year_start(on: date) -> int:
    """Return the year in which the September-based school year started."""
    return on.year if on.month >= 9 else on.year - 1


def parse_receivables(
    annual_payload: object, details_payload: object, *, on: date
) -> Receivables:
    """Parse the two ASP.NET AJAX responses into a validated fee snapshot."""
    try:
        periods = _as_list(annual_payload, "d", "ListData")
        summary = next(
            (
                period
                for period in periods
                if int(period["Rok"]) == on.year and int(period["Miesiac"]) == on.month
            ),
            max(
                periods, key=lambda period: (int(period["Rok"]), int(period["Miesiac"]))
            ),
        )
        costs = {0: Decimal(0), 1: Decimal(0), 2: Decimal(0)}
        for item in _as_list(details_payload, "d", "ListK"):
            kind = int(item["RodzajOplaty"])
            if kind in costs:
                costs[kind] = _amount(item["Kwota"])
        return Receivables(
            summary_to_pay=_amount(summary["DoZaplaty"]),
            summary_paid=_amount(summary["Zaplacono"]),
            summary_overdue=_amount(summary["Zaleglosc"]),
            summary_overpayment=_amount(summary["Nadplata"]),
            costs_fixed=costs[0],
            costs_additional=costs[1],
            costs_meal=costs[2],
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise IprzedszkoleError(
            "iPrzedszkole returned invalid receivables data"
        ) from error


def _as_list(payload: object, *keys: str) -> list[dict[str, Any]]:
    value: Any = payload
    for key in keys:
        value = value[key]
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return value


def _amount(value: object) -> Decimal:
    return Decimal(str(value).replace(",", "."))


class IprzedszkoleClient:
    """Log in to one iPrzedszkole account and fetch its receivables."""

    def __init__(
        self,
        *,
        kindergarten: str,
        login: str,
        password: str,
        timeout: float = 30.0,
        opener_factory: Callable[..., Any] = build_opener,
    ) -> None:
        self._kindergarten = kindergarten
        self._login = login
        self._password = password
        self._timeout = timeout
        self._opener_factory = opener_factory
        self._opener: Any | None = None
        self._child_master_id: str | None = None

    def fetch_receivables(self, *, on: date) -> Receivables:
        """Authenticate and return the current portal receivables."""
        self._authenticate()
        self._get(URL_RECEIVABLES)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{URL_BASE}{URL_RECEIVABLES}",
        }
        annual = self._post_json(
            URL_RECEIVABLES_ANNUAL,
            {
                "args": {
                    "dzieckoId": int(self._child_master_id or 0),
                    "rokStart": str(school_year_start(on)),
                    "listViewName": "Szczegoly",
                }
            },
            headers,
        )
        details = self._post_json(
            URL_RECEIVABLES_DATA,
            {"idDziecko": self._child_master_id},
            headers,
        )
        return parse_receivables(annual, details, on=on)

    def _authenticate(self) -> None:
        self._opener = self._opener_factory(HTTPCookieProcessor(CookieJar()))
        tokens = aspnet_tokens(self._get(URL_LOGIN))
        page = self._post_form(
            URL_LOGIN,
            {
                **tokens,
                "ctl00$cphContent$txtDatabase": self._kindergarten,
                "ctl00$cphContent$txtLogin": self._login,
                "ctl00$cphContent$txtPassword": self._password,
                "ctl00$cphContent$ButtonLogin": "Zaloguj",
            },
            {"Referer": f"{URL_BASE}{URL_LOGIN}"},
        )
        if "txtPassword" in page:
            raise IprzedszkoleError("iPrzedszkole rejected the configured credentials")
        meal_plan = self._get(URL_MEAL_PLAN)
        selected_child = re.search(
            r'<option\s+selected="selected"\s+value="(\d+)">', meal_plan
        )
        if selected_child is None:
            raise IprzedszkoleError("iPrzedszkole did not provide a selected child")
        self._child_master_id = selected_child.group(1)

    def _get(self, path: str) -> str:
        return self._open(
            Request(f"{URL_BASE}{path}", headers={"User-Agent": USER_AGENT})
        )

    def _post_form(
        self, path: str, payload: dict[str, str], headers: dict[str, str]
    ) -> str:
        from urllib.parse import urlencode

        return self._open(
            Request(
                f"{URL_BASE}{path}",
                data=urlencode(payload).encode(),
                headers={"User-Agent": USER_AGENT, **headers},
            )
        )

    def _post_json(self, path: str, payload: object, headers: dict[str, str]) -> object:
        body = self._open(
            Request(
                f"{URL_BASE}{path}",
                data=json.dumps(payload).encode(),
                headers={"User-Agent": USER_AGENT, **headers},
            )
        )
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise IprzedszkoleError("iPrzedszkole returned invalid JSON") from error

    def _open(self, request: Request) -> str:
        if self._opener is None:
            raise IprzedszkoleError("iPrzedszkole client is not authenticated")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, UnicodeDecodeError) as error:
            raise IprzedszkoleError("iPrzedszkole request failed") from error
