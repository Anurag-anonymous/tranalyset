from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tranalyset.domain.common.value_objects import Currency, Money, Symbol
from tranalyset.domain.portfolio.models import Holding, PortfolioSnapshot


@dataclass(frozen=True, slots=True)
class ExposureResult:
    total_exposure: Money
    long_exposure: Money
    short_exposure: Money
    net_exposure: Money
    exposure_by_symbol: dict[Symbol, Money]


class PortfolioExposureCalculator:
    @staticmethod
    def calculate_exposure(holdings: dict[str, Holding], currency: Currency) -> ExposureResult:
        total = Decimal("0")
        long_exposure = Decimal("0")
        short_exposure = Decimal("0")
        by_symbol: dict[Symbol, Money] = {}

        for holding in holdings.values():
            value = holding.market_value
            amount = value
            total += amount
            if holding.quantity >= 0:
                long_exposure += amount
            else:
                short_exposure += amount
            by_symbol[holding.instrument.symbol] = Money(amount, currency)

        return ExposureResult(
            total_exposure=Money(total, currency),
            long_exposure=Money(long_exposure, currency),
            short_exposure=Money(short_exposure, currency),
            net_exposure=Money(long_exposure - short_exposure, currency),
            exposure_by_symbol=by_symbol,
        )

    @staticmethod
    def calculate_from_snapshot(snapshot: PortfolioSnapshot) -> ExposureResult:
        return PortfolioExposureCalculator.calculate_exposure(dict(snapshot.holdings), snapshot.cash_balance.amount.currency)
