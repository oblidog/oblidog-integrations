"""Business operations built on the typed e-Kartoteka API client."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from oblidog_integrations.integrations.ekartoteka.api import (
    EkartotekaApi,
    EkartotekaError,
    NotInitializedError,
)
from oblidog_integrations.integrations.ekartoteka.models import (
    FeePeriod,
    MonthlyFeeItem,
    Premises,
)

MVP_ACCOUNT_SYMBOLS = ("204", "206", "210")
MVP_UPDATE_CATEGORIES = ("DK", "DKL", "LI", "NRB", "NL")
MONITORED_UPDATE_CATEGORIES = frozenset(MVP_UPDATE_CATEGORIES)
EKARTOTEKA_TIMEZONE = ZoneInfo("Europe/Warsaw")
logger = structlog.get_logger(__name__)


class EkartotekaResult(BaseModel):
    """Apartment fee and settlement summary returned by ``get_payment_status``."""

    apartment_fee: Decimal
    delta: Decimal
    paid: bool
    force_unpaid: bool
    update_dates: dict[str, datetime]


class CurrentFeeComponents(BaseModel):
    """Itemized charges for the active billing period of one premises."""

    premises: Premises
    period: FeePeriod
    items: list[MonthlyFeeItem]


class IncompleteSettlementDataError(EkartotekaError):
    """Raised when the ledger cannot calculate a complete obligation amount."""


class SettlementSnapshot(BaseModel):
    """Flat e-Kartoteka category-data record for the MVP account set."""

    year: int
    account_204_credit: float | None
    account_204_debit: float | None
    account_204_balance: float | None
    account_206_credit: float | None
    account_206_debit: float | None
    account_206_balance: float | None
    account_210_credit: float | None
    account_210_debit: float | None
    account_210_balance: float | None
    update_dk_at: datetime | None
    update_dkl_at: datetime | None
    update_li_at: datetime | None
    update_nrb_at: datetime | None
    update_nl_at: datetime | None


class Ekartoteka:
    """Apply Oblidog payment-status rules to e-Kartoteka data."""

    def __init__(
        self,
        credentials: dict[str, str] | None = None,
        *,
        api: EkartotekaApi | None = None,
    ) -> None:
        self.api = api or EkartotekaApi(credentials or {})

    def login(self) -> None:
        """Authenticate the underlying API client."""
        self.api.login()

    def get_settlements_sum(self, year: int) -> Decimal:
        """Calculate the balance (debit minus credit) for ``year``."""
        try:
            return sum(
                (
                    item.debit - item.credit
                    for item in self.api.get_settlements(year).results
                ),
                Decimal(0),
            )
        except (TypeError, ValueError) as error:
            raise EkartotekaError(
                "Invalid settlements data from e-Kartoteka"
            ) from error

    def get_current_fees_sum(self) -> Decimal:
        """Return the current gross monthly fee total."""
        return self.api.get_current_fee_total().gross_amount

    def get_obligation_amount_from_settlements(self, on: date) -> Decimal:
        """Return the month's payable total from the settlement ledgers.

        ``Mc`` in e-Kartoteka's annual ledger is zero-indexed (January is 0).
        We include the three accounts represented by the category-data schema:
        owner settlements (204), renovation fund (206), and interest (210).

        Args:
            on: Month of the target Oblidog obligation.

        Returns:
            Sum of ``DoZaplaty`` for the matching month in accounts 204, 206,
            and 210. Payments and running balances are deliberately excluded.
            A missing month on interest account 210 contributes zero: this
            ledger only publishes months with activity. Other accounts and
            their target-month entries remain mandatory.
        """
        month_index = on.month - 1
        accounts = {
            account.symbol: account
            for account in self.api.get_settlements(on.year).results
            if account.symbol in MVP_ACCOUNT_SYMBOLS
        }
        missing_accounts = set(MVP_ACCOUNT_SYMBOLS) - accounts.keys()
        if missing_accounts:
            raise IncompleteSettlementDataError(
                "Missing settlement accounts needed to calculate obligation amount: "
                + ", ".join(sorted(missing_accounts))
            )

        amounts = []
        for symbol in MVP_ACCOUNT_SYMBOLS:
            matching_entries = [
                entry.amount_due
                for entry in self.api.get_annual_ledger(accounts[symbol].id)
                if entry.month == month_index
            ]
            if not matching_entries:
                if symbol == "210":
                    logger.info(
                        "settlement_interest_month_absent",
                        account_symbol=symbol,
                        year=on.year,
                        month=on.month,
                        assumed_amount="0",
                    )
                    continue
                raise IncompleteSettlementDataError(
                    f"Missing month {on.month} in settlement ledger for account {symbol}"
                )
            amounts.extend(matching_entries)
        return sum(amounts, Decimal(0))

    def get_update_stamp(self) -> dict[str, datetime]:
        """Return dates for categories relevant to payment-status rules."""
        return {
            item.category: datetime.combine(
                item.updated_at.date(), datetime.min.time(), tzinfo=UTC
            )
            for item in self.api.get_update_dates().results
            if item.category in MONITORED_UPDATE_CATEGORIES
            and item.updated_at is not None
        }

    def get_settlement_snapshot(self, year: int) -> SettlementSnapshot:
        """Fetch a flat snapshot suitable for category-data charts.

        Account symbols are the MVP schema contract; an unavailable account is
        represented by ``null`` rather than changing the record shape.

        Args:
            year: Settlement year to fetch from e-Kartoteka.

        Returns:
            A schema-stable flat snapshot of account values and update dates.
        """
        accounts = {
            item.symbol: item for item in self.api.get_settlements(year).results
        }
        update_dates = {
            item.category: self._as_ekartoteka_timestamp(item.updated_at)
            for item in self.api.get_update_dates().results
            if item.category in MONITORED_UPDATE_CATEGORIES
            and item.updated_at is not None
        }

        snapshot: dict[str, int | Decimal | datetime | None] = {"year": year}
        for symbol in MVP_ACCOUNT_SYMBOLS:
            account = accounts.get(symbol)
            prefix = f"account_{symbol}"
            snapshot[f"{prefix}_credit"] = account.credit if account else None
            snapshot[f"{prefix}_debit"] = account.debit if account else None
            snapshot[f"{prefix}_balance"] = account.balance if account else None
        for category in MVP_UPDATE_CATEGORIES:
            snapshot[f"update_{category.lower()}_at"] = update_dates.get(category)

        return SettlementSnapshot.model_validate(snapshot)

    @staticmethod
    def _as_ekartoteka_timestamp(value: datetime) -> datetime:
        """Interpret timezone-naive provider timestamps as Europe/Warsaw time."""
        return (
            value.replace(tzinfo=EKARTOTEKA_TIMEZONE) if value.tzinfo is None else value
        )

    def get_current_fee_components(self, on: date) -> list[CurrentFeeComponents]:
        """Return charges due in ``on``'s month from the prior fee period.

        Args:
            on: Month of the target Oblidog obligation.

        Returns:
            Fee-period metadata and itemized charges for each matching premises.
        """
        fee_period_start = self._fee_period_start_for_obligation(on)
        components = []
        for premises in self.api.get_premises().results:
            period = next(
                (
                    candidate
                    for candidate in self.api.get_fee_periods(premises.id).results
                    if candidate.starts_on == fee_period_start
                ),
                None,
            )
            if period is None:
                continue
            items = self.api.get_monthly_fee_items(
                charge_id=period.charge_id,
                premises_id=premises.id,
            )
            components.append(
                CurrentFeeComponents(
                    premises=premises,
                    period=period,
                    items=items.results,
                )
            )
        return components

    def has_current_fee_period(self, on: date) -> bool:
        """Return whether e-Kartoteka published fees due in ``on``'s month.

        Args:
            on: Month of the target Oblidog obligation.

        Returns:
            ``True`` when at least one premises has the matching source period.
        """
        fee_period_start = self._fee_period_start_for_obligation(on)
        for premises in self.api.get_premises().results:
            for period in self.api.get_fee_periods(premises.id).results:
                if period.starts_on == fee_period_start:
                    return True
        return False

    @staticmethod
    def _fee_period_start_for_obligation(on: date) -> date:
        """Map an obligation month to e-Kartoteka's preceding fee period."""
        return (on.replace(day=1) - timedelta(days=1)).replace(day=1)

    def get_payment_status(self) -> EkartotekaResult:
        """Aggregate the current monthly fee and settlement state."""
        now = datetime.now(UTC)
        apartment_fee = self.get_current_fees_sum()
        delta = self.get_settlements_sum(now.year)
        dates = self.get_update_stamp()
        last_li_update = dates.get("LI")
        if last_li_update is None or (now.year, now.month) != (
            last_li_update.year,
            last_li_update.month,
        ):
            return EkartotekaResult(
                apartment_fee=apartment_fee,
                delta=delta,
                paid=False,
                force_unpaid=True,
                update_dates=dates,
            )
        return EkartotekaResult(
            apartment_fee=apartment_fee,
            delta=delta,
            paid=delta <= Decimal(0),
            force_unpaid=now.day >= 25,
            update_dates=dates,
        )


__all__ = [
    "CurrentFeeComponents",
    "Ekartoteka",
    "EkartotekaError",
    "EkartotekaResult",
    "IncompleteSettlementDataError",
    "NotInitializedError",
    "SettlementSnapshot",
]
