from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class VirtualOrder:
    order_id: str
    trade_id: str
    symbol: str
    order_type: str
    quantity: float
    status: str = "open"
    limit_price: float | None = None
    stop_price: float | None = None
    trailing_distance: float | None = None
    highest_price: float | None = None
    submitted_at: datetime | None = None
    eligible_at: datetime | None = None
    strategy_id: str | None = None
    side: str = "long"
    sector: str = "Unclassified"
    correlation_group: str = "Unclassified"

    def to_payload(self) -> dict[str, float | str]:
        payload: dict[str, float | str] = {
            "order_id": self.order_id,
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "status": self.status,
        }
        if self.limit_price is not None:
            payload["limit_price"] = self.limit_price
        if self.stop_price is not None:
            payload["stop_price"] = self.stop_price
        if self.trailing_distance is not None:
            payload["trailing_distance"] = self.trailing_distance
        if self.strategy_id is not None:
            payload["strategy_id"] = self.strategy_id
        payload["side"] = self.side
        payload["sector"] = self.sector
        payload["correlation_group"] = self.correlation_group
        return payload


@dataclass(slots=True)
class VirtualPosition:
    trade_id: str
    symbol: str
    quantity: float
    average_price: float
    last_price: float
    commissions: float = 0.0
    slippage: float = 0.0
    realized_pnl: float = 0.0
    strategy_id: str | None = None
    side: str = "long"
    total_quantity: float = 0.0
    closed_quantity: float = 0.0
    exit_notional: float = 0.0
    sector: str = "Unclassified"
    correlation_group: str = "Unclassified"

    @property
    def unrealized_pnl(self) -> float:
        return (self.last_price - self.average_price) * self.quantity

    @property
    def market_value(self) -> float:
        return self.last_price * self.quantity

    def to_payload(self) -> dict[str, float | str]:
        payload: dict[str, float | str] = {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "average_price": self.average_price,
            "last_price": self.last_price,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "commissions": self.commissions,
            "slippage": self.slippage,
            "sector": self.sector,
            "correlation_group": self.correlation_group,
        }
        if self.strategy_id is not None:
            payload["strategy_id"] = self.strategy_id
        payload["side"] = self.side
        return payload


@dataclass(slots=True)
class PaperTradingState:
    initial_cash: float = 100_000.0
    cash: float = 100_000.0
    positions: dict[str, VirtualPosition] = field(default_factory=dict)
    orders: dict[str, VirtualOrder] = field(default_factory=dict)
    trade_history: list[dict[str, float | str]] = field(default_factory=list)
    event_history: list[dict[str, Any]] = field(default_factory=list)
    equity_history: list[tuple[datetime, float]] = field(default_factory=list)
    peak_equity: float = 100_000.0
    trading_halted: bool = False
    processed_event_keys: set[str] = field(default_factory=set)
    processed_approval_trade_ids: set[str] = field(default_factory=set)
    pending_exit_prices: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def realized_pnl(self) -> float:
        return sum(position.realized_pnl for position in self.positions.values()) + sum(
            float(trade["realized_pnl"]) for trade in self.trade_history
        )

    @property
    def unrealized_pnl(self) -> float:
        return sum(position.unrealized_pnl for position in self.positions.values())

    @property
    def commissions(self) -> float:
        return sum(position.commissions for position in self.positions.values()) + sum(
            float(trade["commissions"]) for trade in self.trade_history
        )

    @property
    def slippage(self) -> float:
        return sum(position.slippage for position in self.positions.values()) + sum(
            float(trade["slippage"]) for trade in self.trade_history
        )

    @property
    def equity(self) -> float:
        return self.cash + sum(position.market_value for position in self.positions.values())

    @property
    def drawdown(self) -> float:
        return 0.0 if self.peak_equity <= 0 else max(0.0, (self.peak_equity - self.equity) / self.peak_equity)
