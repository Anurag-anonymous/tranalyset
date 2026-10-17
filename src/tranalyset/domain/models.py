"""Immutable domain models and business logic for the trading system."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AssetClass(StrEnum):
    EQUITY = "equity"
    FOREX = "forex"
    CRYPTO = "crypto"
    FUTURE = "future"
    OPTION = "option" 


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Instrument:
    """Core domain model representing a financial instrument."""
    symbol: str
    exchange: str
    asset_class: AssetClass
    pip_size: float = 0.01
    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Symbol must not be empty.")
        if not self.exchange:
            raise ValueError("Exchange must not be empty.")
        if self.pip_size <= 0:
            raise ValueError("Pip size must be greater than zero.")
        if self.multiplier <= 0:
            raise ValueError("Multiplier must be greater than zero.")


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Core domain model representing a real-time market quote."""
    instrument: Instrument
    timestamp: datetime
    bid: float
    ask: float
    last: float

    def __post_init__(self) -> None:
        if self.bid < 0 or self.ask < 0 or self.last < 0:
            raise ValueError("Prices must not be negative.")
        if self.bid > self.ask and self.ask > 0:
            raise ValueError("Bid price must not be greater than Ask price.")


@dataclass(frozen=True, slots=True)
class Order:
    """Core domain model representing a trade order."""
    order_id: str
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.order_id:
            raise ValueError("Order ID must not be empty.")
        if self.quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if self.order_type == OrderType.LIMIT and (self.price is None or self.price <= 0):
            raise ValueError("Limit order must have a valid positive price.")

    def with_status(self, status: OrderStatus) -> Order:
        """Create a new copy of order with the updated status."""
        return Order(
            order_id=self.order_id,
            instrument=self.instrument,
            side=self.side,
            order_type=self.order_type,
            quantity=self.quantity,
            price=self.price,
            status=status,
            created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class Trade:
    """Core domain model representing an execution (fill) of an order."""
    trade_id: str
    order_id: str
    instrument: Instrument
    side: OrderSide
    quantity: float
    price: float
    executed_at: datetime
    fee: float = 0.0

    def __post_init__(self) -> None:
        if not self.trade_id:
            raise ValueError("Trade ID must not be empty.")
        if not self.order_id:
            raise ValueError("Order ID must not be empty.")
        if self.quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if self.price <= 0:
            raise ValueError("Price must be greater than zero.")
        if self.fee < 0:
            raise ValueError("Fee must not be negative.")


@dataclass(frozen=True, slots=True)
class Position:
    """Core domain model representing an open position for an instrument."""
    instrument: Instrument
    quantity: float  # Positive for Long, Negative for Short
    average_entry_price: float
    current_price: float = 0.0
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if self.average_entry_price < 0:
            raise ValueError("Average entry price must not be negative.")
        if self.current_price < 0:
            raise ValueError("Current price must not be negative.")

    @property
    def direction(self) -> str:
        if self.quantity > 0:
            return "long"
        if self.quantity < 0:
            return "short"
        return "flat"

    @property
    def unrealized_pnl(self) -> float:
        """Calculate the unrealized P&L of the position."""
        if self.quantity == 0 or self.current_price == 0:
            return 0.0
        
        # PnL = (Current - Entry) * Qty * Multiplier (reversed for Short)
        pnl = (self.current_price - self.average_entry_price) * self.quantity * self.instrument.multiplier
        return round(pnl, 4)

    @property
    def total_pnl(self) -> float:
        return round(self.unrealized_pnl + self.realized_pnl, 4)

    def update_market_price(self, current_price: float) -> Position:
        """Create a new copy of the Position with updated current market price."""
        return Position(
            instrument=self.instrument,
            quantity=self.quantity,
            average_entry_price=self.average_entry_price,
            current_price=current_price,
            realized_pnl=self.realized_pnl,
        )

    def add_trade(self, trade: Trade) -> Position:
        """Apply a new execution trade to this position, returning a new immutable Position."""
        if trade.instrument != self.instrument:
            raise ValueError("Trade instrument does not match position.")

        trade_qty = trade.quantity if trade.side == OrderSide.BUY else -trade.quantity
        new_qty = self.quantity + trade_qty

        if new_qty == 0:
            # Position is closed
            pnl_gain = self._calculate_realized_pnl_for_closing(trade.price, trade_qty)
            return Position(
                instrument=self.instrument,
                quantity=0.0,
                average_entry_price=0.0,
                current_price=trade.price,
                realized_pnl=round(self.realized_pnl + pnl_gain - trade.fee, 4),
            )

        # Same direction addition or scaling down without reversing
        if (self.quantity >= 0 and trade_qty > 0) or (self.quantity <= 0 and trade_qty < 0):
            # Same direction addition: average out entry price
            total_cost = (abs(self.quantity) * self.average_entry_price) + (abs(trade_qty) * trade.price)
            new_avg_price = total_cost / abs(new_qty)
            return Position(
                instrument=self.instrument,
                quantity=round(new_qty, 6),
                average_entry_price=round(new_avg_price, 4),
                current_price=trade.price,
                realized_pnl=round(self.realized_pnl - trade.fee, 4),
            )
        
        # Reducing/scaling down the position (opposite side trade but doesn't reverse direction)
        if (self.quantity > 0 and new_qty > 0) or (self.quantity < 0 and new_qty < 0):
            # Calculate realized PnL on the closed portion
            pnl_gain = self._calculate_realized_pnl_for_closing(trade.price, trade_qty)
            return Position(
                instrument=self.instrument,
                quantity=round(new_qty, 6),
                average_entry_price=self.average_entry_price,
                current_price=trade.price,
                realized_pnl=round(self.realized_pnl + pnl_gain - trade.fee, 4),
            )

        # Reversing direction (e.g. from Long 10 to Short 5)
        # 1. Close out the existing position entirely
        portion_closing_qty = -self.quantity
        pnl_gain = self._calculate_realized_pnl_for_closing(trade.price, portion_closing_qty)
        # 2. Open new position in opposite direction with remaining qty
        portion_opening_qty = new_qty
        return Position(
            instrument=self.instrument,
            quantity=round(portion_opening_qty, 6),
            average_entry_price=trade.price,
            current_price=trade.price,
            realized_pnl=round(self.realized_pnl + pnl_gain - trade.fee, 4),
        )

    def _calculate_realized_pnl_for_closing(self, trade_price: float, trade_qty: float) -> float:
        """PnL contribution when closing/reducing position."""
        # trade_qty is negative for selling (reducing long), positive for buying (reducing short)
        if self.quantity > 0:
            # Long position reduction: sell trade_qty (negative value) closes portion of long
            # closing qty is positive (abs of trade_qty)
            closing_qty = abs(trade_qty)
            return (trade_price - self.average_entry_price) * closing_qty * self.instrument.multiplier
        else:
            # Short position reduction: buy trade_qty (positive value) closes portion of short
            closing_qty = abs(trade_qty)
            return (self.average_entry_price - trade_price) * closing_qty * self.instrument.multiplier


@dataclass(frozen=True, slots=True)
class Signal:
    """Core domain model representing an indicator or strategy trading signal."""
    signal_id: str
    instrument: Instrument
    side: OrderSide
    strength: float  # Normalized between 0.0 and 1.0
    confidence: float  # Normalized between 0.0 and 1.0
    timestamp: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_id:
            raise ValueError("Signal ID must not be empty.")
        if not (0.0 <= self.strength <= 1.0):
            raise ValueError("Strength must be between 0.0 and 1.0.")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Confidence must be between 0.0 and 1.0.")


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Core domain model representing a trading account portfolio."""
    portfolio_id: str
    account_id: str
    balance: float
    positions: Mapping[str, Position] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.portfolio_id:
            raise ValueError("Portfolio ID must not be empty.")
        if not self.account_id:
            raise ValueError("Account ID must not be empty.")
        if self.balance < 0:
            raise ValueError("Balance must not be negative.")

    @property
    def total_unrealized_pnl(self) -> float:
        """Calculate total unrealized P&L across all open positions."""
        pnl = sum(pos.unrealized_pnl for pos in self.positions.values())
        return round(pnl, 4)

    @property
    def total_realized_pnl(self) -> float:
        """Calculate total realized P&L across all positions."""
        pnl = sum(pos.realized_pnl for pos in self.positions.values())
        return round(pnl, 4)

    @property
    def equity(self) -> float:
        """Calculate total portfolio equity (Balance + Unrealized P&L)."""
        return round(self.balance + self.total_unrealized_pnl, 4)

    def update_quote(self, quote: PriceQuote) -> Portfolio:
        """Update position pricing from a new price quote, returning a new immutable Portfolio."""
        key = f"{quote.instrument.symbol}:{quote.instrument.exchange}"
        if key not in self.positions:
            return self

        old_pos = self.positions[key]
        # Choose appropriate price based on position direction
        price = quote.bid if old_pos.quantity > 0 else quote.ask
        if old_pos.quantity == 0:
            price = quote.last

        new_pos = old_pos.update_market_price(price)
        new_positions = dict(self.positions)
        new_positions[key] = new_pos

        return Portfolio(
            portfolio_id=self.portfolio_id,
            account_id=self.account_id,
            balance=self.balance,
            positions=new_positions,
        )

    def apply_trade(self, trade: Trade) -> Portfolio:
        """Apply an execution trade to the portfolio, updating balances and positions."""
        key = f"{trade.instrument.symbol}:{trade.instrument.exchange}"
        new_positions = dict(self.positions)

        if key not in new_positions:
            # Open new position
            qty = trade.quantity if trade.side == OrderSide.BUY else -trade.quantity
            pos = Position(
                instrument=trade.instrument,
                quantity=qty,
                average_entry_price=trade.price,
                current_price=trade.price,
                realized_pnl=-trade.fee,
            )
            new_positions[key] = pos
            # Update cash balance for opening the position
            new_balance = self.balance - (trade.quantity * trade.price if trade.side == OrderSide.BUY else -trade.quantity * trade.price) - trade.fee
        else:
            old_pos = new_positions[key]
            # Pre-trade balance cash flow
            # When buying, cash decreases; when selling, cash increases
            trade_cash_flow = (trade.quantity * trade.price) if trade.side == OrderSide.BUY else -(trade.quantity * trade.price)
            
            # Apply trade to Position logic
            new_pos = old_pos.add_trade(trade)
            new_positions[key] = new_pos
            
            # Balance updates by subtracting the trade cash flow and trade fee
            new_balance = self.balance - trade_cash_flow - trade.fee

        return Portfolio(
            portfolio_id=self.portfolio_id,
            account_id=self.account_id,
            balance=round(new_balance, 4),
            positions=new_positions,
        )
