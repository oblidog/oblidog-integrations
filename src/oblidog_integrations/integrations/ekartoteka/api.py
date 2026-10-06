"""Low-level, typed client for the e-Kartoteka API."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from oblidog_integrations.integrations.ekartoteka.models import (
    AnnualLedgerEntry,
    AuthenticatedUser,
    BankAccountsPage,
    CurrentFeeTotal,
    FeePeriodsPage,
    MonthlyFeeItemsPage,
    PremisesPage,
    SettlementAccountsPage,
    SettlementYear,
    TokenResponse,
    UpdateDatesPage,
)
from oblidog_integrations.retry import retry_provider


class EkartotekaError(RuntimeError):
    """Raised when e-Kartoteka cannot provide a valid response."""


class NotInitializedError(EkartotekaError):
    """Raised when a request is made before a successful login."""


class EkartotekaApi:
    """Execute e-Kartoteka requests and return validated Pydantic models."""

    URL_SETTLEMENTS = (
        "https://e-kartoteka.pl/api/rozrachunki/konta/?id_a_do={}&id_kli={}&rok={}"
    )
    URL_SETTLEMENT_YEARS = (
        "https://e-kartoteka.pl/api/rozrachunki/dostepne_lata/?id_a_do={}&pageSize=20"
    )
    URL_ANNUAL_LEDGER = "https://e-kartoteka.pl/api/rozrachunki/kartoteka_roczna/?id_kli={}&id_kon={}&pageSize=20"
    URL_TOKEN = "https://e-kartoteka.pl/api/api-token-auth/"
    URL_ME = "https://e-kartoteka.pl/api/uzytkownicy/uzytkownicy/me/"
    URL_PREMISES = (
        "https://e-kartoteka.pl/api/oplatymiesieczne/lokale/?id_a_do={}&id_kli={}"
    )
    URL_MONTHLY_FEES_SUM = "https://e-kartoteka.pl/api/oplatymiesieczne/oplatymiesiecznenalokale/suma/?id_a_do={}&id_kli={}"
    URL_FEE_PERIODS = "https://e-kartoteka.pl/api/oplatymiesieczne/okresy/?id_a_do={}&id_kli={}&id_lok={}&pageSize=20"
    URL_BANK_ACCOUNTS = "https://e-kartoteka.pl/api/oplatymiesieczne/nrb/?id_a_do={}&id_kli={}&id_lok={}&pageSize=20"
    URL_MONTHLY_FEE_ITEMS = "https://e-kartoteka.pl/api/oplatymiesieczne/oplatymiesieczneb/?id_nal={}&id_lok={}&id_kli={}&pageSize=80"
    URL_UPDATE_DATES = "https://e-kartoteka.pl/api/uzytkownicy/datyaktualizacji/?id_a_do={}&id_kli={}&pageSize=50"

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = credentials
        self._logged_in = False
        self.token: str | None = None
        self.user: AuthenticatedUser | None = None
        self.client_id: int | None = None

    def _request_json(self, url: str, *, payload: dict[str, str] | None = None) -> Any:
        return retry_provider(
            lambda: self._request_json_once(url, payload=payload),
            integration="ekartoteka",
            operation_name="login" if url == self.URL_TOKEN else "fetch_json",
        )

    def _request_json_once(
        self, url: str, *, payload: dict[str, str] | None = None
    ) -> Any:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with urlopen(
                Request(url, data=data, headers=headers), timeout=30
            ) as response:
                return json.load(response)
        except (HTTPError, URLError) as error:
            raise EkartotekaError(
                f"e-Kartoteka request failed: {error.reason}"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EkartotekaError("e-Kartoteka returned invalid JSON") from error

    @staticmethod
    def _parse(model: type[BaseModel], data: Any, response_name: str) -> Any:
        try:
            return model.model_validate(data)
        except ValidationError as error:
            raise EkartotekaError(
                f"Invalid {response_name} response from e-Kartoteka"
            ) from error

    def _get_paginated(
        self, model: type[BaseModel], url: str, response_name: str
    ) -> Any:
        """Fetch every page in an e-Kartoteka paginated response."""
        page = self._parse(model, self._request_json(url), response_name)
        results = list(page.results)
        while page.next is not None:
            page = self._parse(model, self._request_json(page.next), response_name)
            results.extend(page.results)
        return page.model_copy(
            update={
                "count": len(results),
                "next": None,
                "previous": None,
                "results": results,
            }
        )

    def _decode_client_id(self) -> int:
        if self.token is None:
            raise NotInitializedError("No token received from e-Kartoteka")
        try:
            encoded_payload = self.token.split(".")[1]
            encoded_payload += "=" * (-len(encoded_payload) % 4)
            payload = json.loads(urlsafe_b64decode(encoded_payload).decode("utf-8"))
            return int(str(payload["username"]).split("_", maxsplit=1)[0])
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
        ) as error:
            raise EkartotekaError("Invalid e-Kartoteka token payload") from error

    def login(self) -> AuthenticatedUser:
        """Authenticate and return the current API user."""
        token = self._parse(
            TokenResponse,
            self._request_json(self.URL_TOKEN, payload=self._credentials),
            "token",
        )
        self.token = token.token
        self.client_id = self._decode_client_id()
        self.user = self._parse(
            AuthenticatedUser, self._request_json(self.URL_ME), "user"
        )
        try:
            _ = self.user.accounting_id
        except ValueError as error:
            raise EkartotekaError("Invalid user response from e-Kartoteka") from error
        self._logged_in = True
        return self.user

    def _require_login(self) -> tuple[int, int]:
        if not self._logged_in or self.token is None:
            raise NotInitializedError("Call login() before fetching e-Kartoteka data")
        if self.user is None or self.client_id is None:
            raise NotInitializedError("e-Kartoteka account identifiers are unavailable")
        return self.user.accounting_id, self.client_id

    def get_available_settlement_years(self) -> list[SettlementYear]:
        user_id, _ = self._require_login()
        try:
            return [
                SettlementYear.model_validate(item)
                for item in self._request_json(
                    self.URL_SETTLEMENT_YEARS.format(user_id)
                )
            ]
        except (TypeError, ValidationError) as error:
            raise EkartotekaError(
                "Invalid settlement-years response from e-Kartoteka"
            ) from error

    def get_settlements(self, year: int) -> SettlementAccountsPage:
        user_id, client_id = self._require_login()
        return self._get_paginated(
            SettlementAccountsPage,
            self.URL_SETTLEMENTS.format(user_id, client_id, year),
            "settlements",
        )

    def get_annual_ledger(self, account_id: int) -> list[AnnualLedgerEntry]:
        _, client_id = self._require_login()
        try:
            return [
                AnnualLedgerEntry.model_validate(item)
                for item in self._request_json(
                    self.URL_ANNUAL_LEDGER.format(client_id, account_id)
                )
            ]
        except (TypeError, ValidationError) as error:
            raise EkartotekaError(
                "Invalid annual-ledger response from e-Kartoteka"
            ) from error

    def get_premises(self) -> PremisesPage:
        user_id, client_id = self._require_login()
        return self._get_paginated(
            PremisesPage,
            self.URL_PREMISES.format(user_id, client_id),
            "premises",
        )

    def get_fee_periods(self, premises_id: int) -> FeePeriodsPage:
        user_id, client_id = self._require_login()
        return self._get_paginated(
            FeePeriodsPage,
            self.URL_FEE_PERIODS.format(user_id, client_id, premises_id),
            "fee-periods",
        )

    def get_bank_accounts(self, premises_id: int) -> BankAccountsPage:
        user_id, client_id = self._require_login()
        return self._get_paginated(
            BankAccountsPage,
            self.URL_BANK_ACCOUNTS.format(user_id, client_id, premises_id),
            "bank-accounts",
        )

    def get_monthly_fee_items(
        self, *, charge_id: int, premises_id: int
    ) -> MonthlyFeeItemsPage:
        _, client_id = self._require_login()
        return self._get_paginated(
            MonthlyFeeItemsPage,
            self.URL_MONTHLY_FEE_ITEMS.format(charge_id, premises_id, client_id),
            "monthly-fee-items",
        )

    def get_current_fee_total(self) -> CurrentFeeTotal:
        user_id, client_id = self._require_login()
        data = self._request_json(self.URL_MONTHLY_FEES_SUM.format(user_id, client_id))
        try:
            return CurrentFeeTotal.model_validate(data[0])
        except (IndexError, TypeError, ValidationError) as error:
            raise EkartotekaError(
                "Invalid monthly-fee response from e-Kartoteka"
            ) from error

    def get_update_dates(self) -> UpdateDatesPage:
        user_id, client_id = self._require_login()
        return self._get_paginated(
            UpdateDatesPage,
            self.URL_UPDATE_DATES.format(user_id, client_id),
            "update-date",
        )
