from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tranalyset.domain.common import AssetClass, OrderSide
from tranalyset.domain.common.value_objects import Currency, Money, Price, Quantity, Ratio, Symbol
from tranalyset.domain.market.models import Exchange, Instrument
from tranalyset.domain.portfolio import (
    AssetAllocation,
    CashBalance,
    Drawdown,
    EquityCurve,
    EquityPoint,
    Exposure,
    Holding,
    PnL,
    Portfolio,
    PortfolioSnapshot,
    PortfolioStatistics,
    SectorExposure,
)
from tranalyset.domain.trading import Trade


def _instrument() -> Instrument:
    return Instrument(symbol=Symbol("AAPL"), exchange=Exchange("NASDAQ"), asset_class=AssetClass.EQUITY)


def _now() -> datetime:
    return datetime(2026, 7, 26, 9, 30, tzinfo=UTC)


def test_holding_updates_immutably_and_calculates_pnl() -> None:
    holding = Holding(instrument=_instrument(), quantity=10, average_entry_price=100, current_price=105)

    updated = holding.update_market_price(110)

    assert holding.current_price == Decimal("105.000000")
    assert updated.current_price == Decimal("110.000000")
    assert updated.unrealized_pnl == Decimal("100.000000")


def test_cash_balance_and_limits_validate() -> None:
    balance = CashBalance(amount=Money(1000, Currency("USD")), reserved=Money(100, Currency("USD")))

    assert balance.available == Money(900, Currency("USD"))
    assert balance.deposit(Money(50, Currency("USD"))).amount == Money(1050, Currency("USD"))
    with pytest.raises(ValueError, match="cannot go negative"):
        balance.withdraw(Money(2000, Currency("USD")))


def test_portfolio_snapshot_and_statistics_are_immutable() -> None:
    holding = Holding(instrument=_instrument(), quantity=10, average_entry_price=100, current_price=105, sector="Technology")
    portfolio = Portfolio(portfolio_id="p-1", account_id="acc-1", balance=1000, positions={"AAPL:NASDAQ": holding})

    snapshot = portfolio.snapshot(captured_at=_now())

    assert snapshot.asset_allocation.allocations[AssetClass.EQUITY] == Ratio(1)
    assert snapshot.pnl is not None
    assert snapshot.statistics is not None
    assert snapshot.statistics.holding_count == 1
    assert hash(snapshot)


def test_portfolio_apply_trade_updates_balance_and_positions() -> None:
    portfolio = Portfolio(portfolio_id="p-1", account_id="acc-1", balance=10_000)
    trade = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=_instrument(),
        side=OrderSide.BUY,
        quantity=Quantity(10),
        price=Price(100),
        executed_at=_now(),
        fee=Price(10),
    )

    updated = portfolio.apply_trade(trade)

    assert updated.balance == Decimal("8990.000000")
    assert updated.positions["AAPL:NASDAQ"].quantity == Decimal("10.000000")


def test_asset_allocation_drawdown_and_equity_curve_validate() -> None:
    allocation = AssetAllocation(allocations={AssetClass.EQUITY: Ratio(1)})
    drawdown = Drawdown(
        peak_equity=Money(1000, Currency("USD")),
        current_equity=Money(900, Currency("USD")),
        trough_equity=Money(850, Currency("USD")),
    )
    equity_curve = EquityCurve(points=(EquityPoint(timestamp=_now(), equity=Money(1000, Currency("USD"))),))

    assert allocation.allocations[AssetClass.EQUITY] == Ratio(1)
    assert drawdown.value == Ratio(0.1)
    assert equity_curve.latest is not None

    with pytest.raises(ValueError, match="sum to 1.0"):
        AssetAllocation(allocations={AssetClass.EQUITY: Ratio(0)})


def test_exposure_and_sector_exposure_are_validated() -> None:
    exposure = Exposure(asset_class=AssetClass.EQUITY, amount=Money(2500, Currency("USD")))
    sector_exposure = SectorExposure(sector="Technology", amount=Money(2500, Currency("USD")), weight=Ratio(0.5))

    assert exposure.amount == Money(2500, Currency("USD"))
    assert sector_exposure.weight == Ratio(0.5)

    with pytest.raises(ValueError, match="must not be negative"):
        Exposure(asset_class=AssetClass.EQUITY, amount=Money(-1, Currency("USD")))


def test_portfolio_statistics_return_calculation() -> None:
    equity_curve = EquityCurve(
        points=(
            EquityPoint(timestamp=_now(), equity=Money(1000, Currency("USD"))),
            EquityPoint(timestamp=_now().replace(minute=31), equity=Money(1100, Currency("USD"))),
        )
    )
    stats = PortfolioStatistics(
        captured_at=_now(),
        pnl=PnL(realized=Money(20, Currency("USD")), unrealized=Money(80, Currency("USD"))),
        drawdown=Drawdown(Money(1100, Currency("USD")), Money(1050, Currency("USD")), Money(1000, Currency("USD"))),
        asset_allocation=AssetAllocation(allocations={AssetClass.EQUITY: Ratio(1)}),
        equity_curve=equity_curve,
        holding_count=1,
        exposure_count=1,
        sector_count=1,
    )

    assert stats.total_return == Money(100, Currency("USD"))
