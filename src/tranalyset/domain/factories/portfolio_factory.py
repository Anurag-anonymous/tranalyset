from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tranalyset.domain.common.value_objects import Currency, Money
from tranalyset.domain.portfolio.models import CashBalance, Portfolio, PortfolioSnapshot


@dataclass(frozen=True, slots=True)
class PortfolioFactory:
    @staticmethod
    def create_portfolio(
        portfolio_id: str,
        account_id: str,
        balance: float | int | str,
        *,
        currency: Currency = Currency("USD"),
    ) -> Portfolio:
        return Portfolio(portfolio_id=portfolio_id, account_id=account_id, balance=balance, currency=currency)

    @staticmethod
    def create_cash_balance(amount: float | int | str, *, currency: Currency = Currency("USD")) -> CashBalance:
        return CashBalance(amount=Money(amount, currency))

    @staticmethod
    def create_snapshot(
        portfolio_id: str,
        account_id: str,
        captured_at: datetime,
        cash_balance: CashBalance,
    ) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            portfolio_id=portfolio_id,
            account_id=account_id,
            captured_at=captured_at,
            cash_balance=cash_balance,
        )
