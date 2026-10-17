from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from tranalyset.domain.common import AssetClass, PositionDirection
from tranalyset.domain.common.value_objects import (
    Currency,
    Liquidity,
    Money,
    Price,
    Ratio,
    Spread,
    Volume,
    quantize_money,
)
from tranalyset.domain.market.models import Instrument, PriceQuote
from tranalyset.domain.trading.models import Trade


def _decimal(value: Decimal | int | float | str) -> Decimal:
    return quantize_money(value if isinstance(value, Decimal) else Decimal(str(value)))


def _require_aware(timestamp: datetime, field_name: str) -> None:
    if timestamp.tzinfo is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)


def _freeze_mapping(mapping: Mapping[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(dict(mapping))


class PortfolioDomainError(ValueError):
    pass


class AllocationMode(StrEnum):
    PERCENT = "percent"
    VALUE = "value"


@dataclass(frozen=True, slots=True)
class Holding:
    instrument: Instrument
    quantity: Decimal | int | float | str
    average_entry_price: Decimal | int | float | str
    current_price: Decimal | int | float | str = Decimal("0")
    realized_pnl: Decimal | int | float | str = Decimal("0")
    sector: str | None = None

    def __post_init__(self) -> None:
        quantity = _decimal(self.quantity)
        average_entry_price = _decimal(self.average_entry_price)
        current_price = _decimal(self.current_price)
        realized_pnl = _decimal(self.realized_pnl)
        if average_entry_price < 0:
            msg = "Average entry price must not be negative."
            raise ValueError(msg)
        if current_price < 0:
            msg = "Current price must not be negative."
            raise ValueError(msg)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "average_entry_price", average_entry_price)
        object.__setattr__(self, "current_price", current_price)
        object.__setattr__(self, "realized_pnl", realized_pnl)
        if self.sector is not None and not self.sector.strip():
            msg = "Sector must not be empty."
            raise ValueError(msg)

    @property
    def direction(self) -> str:
        if self.quantity > 0:
            return PositionDirection.LONG.value
        if self.quantity < 0:
            return PositionDirection.SHORT.value
        return PositionDirection.FLAT.value

    @property
    def market_value(self) -> Decimal:
        return quantize_money(abs(self.quantity) * self.current_price * Decimal(str(self.instrument.multiplier)))

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.quantity == 0 or self.current_price == 0:
            return Decimal("0.000000")
        return quantize_money(
            (self.current_price - self.average_entry_price)
            * self.quantity
            * Decimal(str(self.instrument.multiplier))
        )

    @property
    def total_pnl(self) -> Decimal:
        return quantize_money(self.unrealized_pnl + self.realized_pnl)

    def update_market_price(self, current_price: Decimal | int | float | str) -> Holding:
        return type(self)(
            instrument=self.instrument,
            quantity=self.quantity,
            average_entry_price=self.average_entry_price,
            current_price=current_price,
            realized_pnl=self.realized_pnl,
            sector=self.sector,
        )

    def add_trade(self, trade: Trade) -> Holding:
        if trade.instrument != self.instrument:
            msg = "Trade instrument does not match holding."
            raise ValueError(msg)

        trade_quantity = trade.signed_quantity
        new_quantity = self.quantity + trade_quantity
        fee = trade.fee.value

        if self.quantity == 0:
            return type(self)(
                instrument=self.instrument,
                quantity=new_quantity,
                average_entry_price=trade.price.value if new_quantity != 0 else Decimal("0"),
                current_price=trade.price.value,
                realized_pnl=self.realized_pnl - fee,
                sector=self.sector,
            )

        if _same_direction(self.quantity, trade_quantity):
            total_cost = abs(self.quantity) * self.average_entry_price + abs(trade_quantity) * trade.price.value
            average_price = total_cost / abs(new_quantity)
            return type(self)(
                instrument=self.instrument,
                quantity=new_quantity,
                average_entry_price=average_price,
                current_price=trade.price.value,
                realized_pnl=self.realized_pnl - fee,
                sector=self.sector,
            )

        closed_quantity = min(abs(self.quantity), abs(trade_quantity))
        pnl = self._realized_pnl_for_close(trade.price.value, closed_quantity)
        realized = self.realized_pnl + pnl - fee
        if new_quantity == 0:
            average_price = Decimal("0")
        elif _same_direction(self.quantity, new_quantity):
            average_price = self.average_entry_price
        else:
            average_price = trade.price.value

        return type(self)(
            instrument=self.instrument,
            quantity=new_quantity,
            average_entry_price=average_price,
            current_price=trade.price.value,
            realized_pnl=realized,
            sector=self.sector,
        )

    def _realized_pnl_for_close(self, trade_price: Decimal, closing_quantity: Decimal) -> Decimal:
        if self.quantity > 0:
            return quantize_money(
                (trade_price - self.average_entry_price)
                * closing_quantity
                * Decimal(str(self.instrument.multiplier))
            )
        return quantize_money(
            (self.average_entry_price - trade_price)
            * closing_quantity
            * Decimal(str(self.instrument.multiplier))
        )


Position = Holding


@dataclass(frozen=True, slots=True)
class CashBalance:
    amount: Money
    reserved: Money | None = None

    def __post_init__(self) -> None:
        if self.amount.amount < 0:
            msg = "Cash balance must not be negative."
            raise ValueError(msg)
        if self.reserved is not None and self.reserved.amount < 0:
            msg = "Reserved cash must not be negative."
            raise ValueError(msg)
        if self.reserved is not None and self.reserved.currency != self.amount.currency:
            msg = "Cash balance and reserved cash must use the same currency."
            raise ValueError(msg)

    @property
    def available(self) -> Money:
        if self.reserved is None:
            return self.amount
        return self.amount - self.reserved

    def deposit(self, value: Money) -> CashBalance:
        if value.currency != self.amount.currency:
            msg = "CashBalance currency mismatch."
            raise ValueError(msg)
        return type(self)(amount=self.amount + value, reserved=self.reserved)

    def withdraw(self, value: Money) -> CashBalance:
        if value.currency != self.amount.currency:
            msg = "CashBalance currency mismatch."
            raise ValueError(msg)
        next_amount = self.amount - value
        if next_amount.amount < 0:
            msg = "Cash balance cannot go negative."
            raise ValueError(msg)
        return type(self)(amount=next_amount, reserved=self.reserved)


@dataclass(frozen=True, slots=True)
class Exposure:
    asset_class: AssetClass
    amount: Money

    def __post_init__(self) -> None:
        if self.amount.amount < 0:
            msg = "Exposure amount must not be negative."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SectorExposure:
    sector: str
    amount: Money
    weight: Ratio | None = None

    def __post_init__(self) -> None:
        if not self.sector.strip():
            msg = "Sector must not be empty."
            raise ValueError(msg)
        if self.amount.amount < 0:
            msg = "Sector exposure amount must not be negative."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AssetAllocation:
    allocations: Mapping[AssetClass, Ratio]
    mode: AllocationMode = AllocationMode.PERCENT

    def __post_init__(self) -> None:
        if not self.allocations:
            msg = "AssetAllocation must not be empty."
            raise ValueError(msg)
        total = Decimal("0")
        frozen: dict[AssetClass, Ratio] = {}
        for asset_class, ratio in self.allocations.items():
            if not isinstance(asset_class, AssetClass):
                msg = "AssetAllocation keys must be AssetClass values."
                raise ValueError(msg)
            if not isinstance(ratio, Ratio):
                ratio = Ratio.bounded(ratio, field_name=f"allocation for {asset_class}")
            total += ratio.value
            frozen[asset_class] = ratio
        if self.mode is AllocationMode.PERCENT and abs(total - Decimal("1")) > Decimal("0.000001"):
            msg = "Asset allocation percentages must sum to 1.0."
            raise ValueError(msg)
        object.__setattr__(self, "allocations", MappingProxyType(frozen))

    def __hash__(self) -> int:
        return hash((self.mode, tuple(sorted(self.allocations.items(), key=lambda item: item[0].value))))


@dataclass(frozen=True, slots=True)
class PnL:
    realized: Money
    unrealized: Money
    commissions: Money = field(default_factory=lambda: Money(0))
    slippage: Money = field(default_factory=lambda: Money(0))

    def __post_init__(self) -> None:
        _require_same_currency((self.realized, self.unrealized, self.commissions, self.slippage))

    @property
    def total(self) -> Money:
        return self.realized + self.unrealized - self.commissions - self.slippage


@dataclass(frozen=True, slots=True)
class Drawdown:
    peak_equity: Money
    current_equity: Money
    trough_equity: Money

    def __post_init__(self) -> None:
        _require_same_currency((self.peak_equity, self.current_equity, self.trough_equity))
        if self.peak_equity.amount < 0 or self.current_equity.amount < 0 or self.trough_equity.amount < 0:
            msg = "Drawdown equity values must not be negative."
            raise ValueError(msg)
        if self.peak_equity.amount < self.trough_equity.amount:
            msg = "Peak equity must be >= trough equity."
            raise ValueError(msg)

    @property
    def value(self) -> Ratio:
        if self.peak_equity.amount == 0:
            return Ratio(0)
        return Ratio((self.peak_equity.amount - self.current_equity.amount) / self.peak_equity.amount)

    def update(self, equity: Money) -> Drawdown:
        if equity.currency != self.peak_equity.currency:
            msg = "Drawdown currency mismatch."
            raise ValueError(msg)
        peak = self.peak_equity if self.peak_equity.amount >= equity.amount else equity
        trough = self.trough_equity if self.trough_equity.amount <= equity.amount else equity
        return type(self)(peak_equity=peak, current_equity=equity, trough_equity=trough)


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Money

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "EquityPoint timestamp")


@dataclass(frozen=True, slots=True)
class EquityCurve:
    points: tuple[EquityPoint, ...] = ()

    def __post_init__(self) -> None:
        if self.points:
            currency = self.points[0].equity.currency
            last_timestamp = self.points[0].timestamp
            for point in self.points[1:]:
                _require_aware(point.timestamp, "EquityPoint timestamp")
                if point.equity.currency != currency:
                    msg = "EquityCurve points must use the same currency."
                    raise ValueError(msg)
                if point.timestamp <= last_timestamp:
                    msg = "EquityCurve timestamps must be strictly increasing."
                    raise ValueError(msg)
                last_timestamp = point.timestamp
        object.__setattr__(self, "points", tuple(self.points))

    @property
    def latest(self) -> EquityPoint | None:
        if not self.points:
            return None
        return self.points[-1]

    def __hash__(self) -> int:
        return hash(self.points)

    def append(self, point: EquityPoint) -> EquityCurve:
        if self.points and point.equity.currency != self.points[0].equity.currency:
            msg = "EquityCurve currency mismatch."
            raise ValueError(msg)
        if self.points and point.timestamp <= self.points[-1].timestamp:
            msg = "EquityCurve timestamps must be strictly increasing."
            raise ValueError(msg)
        return type(self)(points=self.points + (point,))


@dataclass(frozen=True, slots=True)
class PortfolioStatistics:
    captured_at: datetime
    pnl: PnL
    drawdown: Drawdown
    asset_allocation: AssetAllocation
    equity_curve: EquityCurve
    holding_count: int
    exposure_count: int
    sector_count: int

    def __post_init__(self) -> None:
        _require_aware(self.captured_at, "PortfolioStatistics captured_at")
        if self.holding_count < 0 or self.exposure_count < 0 or self.sector_count < 0:
            msg = "Portfolio statistics counts must not be negative."
            raise ValueError(msg)

    @property
    def total_return(self) -> Money:
        if self.equity_curve.latest is None:
            return self.pnl.total
        start = self.equity_curve.points[0].equity
        end = self.equity_curve.latest.equity
        return end - start

    def __hash__(self) -> int:
        return hash(
            (
                self.captured_at,
                self.pnl,
                self.drawdown,
                self.asset_allocation,
                self.equity_curve,
                self.holding_count,
                self.exposure_count,
                self.sector_count,
            )
        )


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    portfolio_id: str
    account_id: str
    captured_at: datetime
    cash_balance: CashBalance
    holdings: Mapping[str, Holding] = field(default_factory=dict)
    exposures: Mapping[AssetClass, Exposure] = field(default_factory=dict)
    sector_exposures: Mapping[str, SectorExposure] = field(default_factory=dict)
    asset_allocation: AssetAllocation | None = None
    pnl: PnL | None = None
    drawdown: Drawdown | None = None
    equity_curve: EquityCurve = field(default_factory=EquityCurve)
    statistics: PortfolioStatistics | None = None

    def __post_init__(self) -> None:
        if not self.portfolio_id:
            msg = "PortfolioSnapshot portfolio_id must not be empty."
            raise ValueError(msg)
        if not self.account_id:
            msg = "PortfolioSnapshot account_id must not be empty."
            raise ValueError(msg)
        _require_aware(self.captured_at, "PortfolioSnapshot captured_at")
        object.__setattr__(self, "holdings", MappingProxyType(dict(self.holdings)))
        object.__setattr__(self, "exposures", MappingProxyType(dict(self.exposures)))
        object.__setattr__(self, "sector_exposures", MappingProxyType(dict(self.sector_exposures)))
        if self.asset_allocation is None:
            object.__setattr__(self, "asset_allocation", _allocation_from_holdings(self.holdings))
        if self.pnl is None:
            object.__setattr__(self, "pnl", _pnl_from_holdings(self.cash_balance, self.holdings))
        if self.drawdown is None:
            equity = self.pnl.total + self.cash_balance.amount if self.pnl is not None else self.cash_balance.amount
            object.__setattr__(self, "drawdown", Drawdown(equity, equity, equity))
        if self.statistics is None:
            object.__setattr__(self, "statistics", self._statistics())

    def __hash__(self) -> int:
        return hash(
            (
                self.portfolio_id,
                self.account_id,
                self.captured_at,
                self.cash_balance,
                tuple(sorted(self.holdings.items())),
                tuple(sorted(self.exposures.items(), key=lambda item: item[0].value)),
                tuple(sorted(self.sector_exposures.items())),
                self.asset_allocation,
                self.pnl,
                self.drawdown,
                self.equity_curve,
                self.statistics,
            )
        )

    def _statistics(self) -> PortfolioStatistics:
        return PortfolioStatistics(
            captured_at=self.captured_at,
            pnl=self.pnl or _pnl_from_holdings(self.cash_balance, self.holdings),
            drawdown=self.drawdown or Drawdown(self.pnl.total + self.cash_balance.amount if self.pnl is not None else self.cash_balance.amount,
                                               self.pnl.total + self.cash_balance.amount if self.pnl is not None else self.cash_balance.amount,
                                               self.pnl.total + self.cash_balance.amount if self.pnl is not None else self.cash_balance.amount),
            asset_allocation=self.asset_allocation or _allocation_from_holdings(self.holdings),
            equity_curve=self.equity_curve,
            holding_count=len(self.holdings),
            exposure_count=len(self.exposures),
            sector_count=len(self.sector_exposures),
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.portfolio_id,
                self.account_id,
                self.captured_at,
                self.cash_balance,
                tuple(sorted(self.holdings.items())),
                tuple(sorted(self.exposures.items(), key=lambda item: item[0].value)),
                tuple(sorted(self.sector_exposures.items())),
                self.asset_allocation,
                self.pnl,
                self.drawdown,
                self.equity_curve,
                self.statistics,
            )
        )


@dataclass(frozen=True, slots=True)
class Portfolio:
    portfolio_id: str
    account_id: str
    balance: Decimal | int | float | str
    positions: Mapping[str, Holding] = field(default_factory=dict)
    currency: Currency = Currency("USD")

    def __post_init__(self) -> None:
        if not self.portfolio_id:
            msg = "Portfolio ID must not be empty."
            raise ValueError(msg)
        if not self.account_id:
            msg = "Account ID must not be empty."
            raise ValueError(msg)
        balance = _decimal(self.balance)
        if balance < 0:
            msg = "Balance must not be negative."
            raise ValueError(msg)
        object.__setattr__(self, "balance", balance)
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))

    def __hash__(self) -> int:
        return hash((self.portfolio_id, self.account_id, self.balance, self.currency, tuple(sorted(self.positions.items()))))

    @property
    def cash_balance(self) -> CashBalance:
        return CashBalance(amount=Money(self.balance, self.currency))

    @property
    def holdings(self) -> Mapping[str, Holding]:
        return self.positions

    @property
    def total_unrealized_pnl(self) -> Decimal:
        return quantize_money(sum((holding.unrealized_pnl for holding in self.positions.values()), Decimal("0")))

    @property
    def total_realized_pnl(self) -> Decimal:
        return quantize_money(sum((holding.realized_pnl for holding in self.positions.values()), Decimal("0")))

    @property
    def equity(self) -> Decimal:
        return quantize_money(self.balance + self.total_unrealized_pnl)

    def snapshot(self, *, captured_at: datetime | None = None) -> PortfolioSnapshot:
        captured = captured_at or datetime.now(UTC)
        return PortfolioSnapshot(
            portfolio_id=self.portfolio_id,
            account_id=self.account_id,
            captured_at=captured,
            cash_balance=self.cash_balance,
            holdings=self.positions,
            exposures=_exposures_from_holdings(self.positions, self.currency),
            sector_exposures=_sector_exposures_from_holdings(self.positions, self.currency),
            pnl=_pnl_from_holdings(self.cash_balance, self.positions),
            drawdown=Drawdown(Money(self.equity, self.currency), Money(self.equity, self.currency), Money(self.equity, self.currency)),
            equity_curve=EquityCurve(
                points=(EquityPoint(timestamp=captured, equity=Money(self.equity, self.currency)),)
            ),
        )

    def update_quote(self, quote: PriceQuote) -> Portfolio:
        key = quote.instrument.instrument_id
        holding = self.positions.get(key)
        if holding is None:
            return self
        mark = quote.bid if holding.quantity > 0 else quote.ask if holding.quantity < 0 else quote.last
        positions = dict(self.positions)
        positions[key] = holding.update_market_price(mark.value)
        return type(self)(
            portfolio_id=self.portfolio_id,
            account_id=self.account_id,
            balance=self.balance,
            positions=positions,
            currency=self.currency,
        )

    def apply_trade(self, trade: Trade) -> Portfolio:
        key = trade.instrument.instrument_id
        positions = dict(self.positions)
        holding = positions.get(
            key,
            Holding(
                instrument=trade.instrument,
                quantity=Decimal("0"),
                average_entry_price=Decimal("0"),
                current_price=trade.price.value,
            ),
        )
        updated_holding = holding.add_trade(trade)
        positions[key] = updated_holding
        balance = self.balance + trade.cash_flow
        return type(self)(
            portfolio_id=self.portfolio_id,
            account_id=self.account_id,
            balance=balance,
            positions=positions,
            currency=self.currency,
        )


def _same_direction(left: Decimal, right: Decimal) -> bool:
    return (left >= 0 and right > 0) or (left <= 0 and right < 0)


def _require_same_currency(money_values: Sequence[Money]) -> None:
    if not money_values:
        return
    currency = money_values[0].currency
    for money in money_values[1:]:
        if money.currency != currency:
            msg = "Money values must share the same currency."
            raise ValueError(msg)


def _allocation_from_holdings(holdings: Mapping[str, Holding]) -> AssetAllocation:
    if not holdings:
        return AssetAllocation(allocations={AssetClass.EQUITY: Ratio(1)})
    totals: dict[AssetClass, Decimal] = {}
    total_value = Decimal("0")
    for holding in holdings.values():
        value = holding.market_value
        totals[holding.instrument.asset_class] = totals.get(holding.instrument.asset_class, Decimal("0")) + value
        total_value += value
    if total_value == 0:
        return AssetAllocation(allocations={AssetClass.EQUITY: Ratio(1)})
    allocations = {
        asset_class: Ratio(value / total_value)
        for asset_class, value in totals.items()
    }
    return AssetAllocation(allocations=allocations)


def _exposures_from_holdings(holdings: Mapping[str, Holding], currency: Currency) -> Mapping[AssetClass, Exposure]:
    exposures: dict[AssetClass, Exposure] = {}
    for holding in holdings.values():
        current = exposures.get(holding.instrument.asset_class)
        amount = Money(holding.market_value, currency)
        if current is None:
            exposures[holding.instrument.asset_class] = Exposure(asset_class=holding.instrument.asset_class, amount=amount)
        else:
            exposures[holding.instrument.asset_class] = Exposure(
                asset_class=holding.instrument.asset_class,
                amount=current.amount + amount,
            )
    return MappingProxyType(exposures)


def _sector_exposures_from_holdings(holdings: Mapping[str, Holding], currency: Currency) -> Mapping[str, SectorExposure]:
    exposures: dict[str, SectorExposure] = {}
    total_value = sum((holding.market_value for holding in holdings.values()), Decimal("0"))
    for holding in holdings.values():
        if holding.sector is None:
            continue
        amount = Money(holding.market_value, currency)
        ratio = Ratio(0 if total_value == 0 else holding.market_value / total_value)
        current = exposures.get(holding.sector)
        if current is None:
            exposures[holding.sector] = SectorExposure(sector=holding.sector, amount=amount, weight=ratio)
        else:
            exposures[holding.sector] = SectorExposure(
                sector=holding.sector,
                amount=current.amount + amount,
                weight=ratio,
            )
    return MappingProxyType(exposures)


def _pnl_from_holdings(cash_balance: CashBalance, holdings: Mapping[str, Holding]) -> PnL:
    realized = sum((holding.realized_pnl for holding in holdings.values()), Decimal("0"))
    unrealized = sum((holding.unrealized_pnl for holding in holdings.values()), Decimal("0"))
    currency = cash_balance.amount.currency
    return PnL(
        realized=Money(realized, currency),
        unrealized=Money(unrealized, currency),
        commissions=Money(0, currency),
        slippage=Money(0, currency),
    )
