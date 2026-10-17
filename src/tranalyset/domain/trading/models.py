from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from tranalyset.domain.common import OrderSide, OrderStatus, OrderType
from tranalyset.domain.common.value_objects import Price, Quantity, quantize_money
from tranalyset.domain.market.models import Instrument


class TradingDomainError(ValueError):
    pass


class OrderTransitionError(TradingDomainError):
    pass


class TradeTransitionError(TradingDomainError):
    pass


def _aware_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(timestamp: datetime, field_name: str) -> None:
    if timestamp.tzinfo is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)


def _decimal(value: Decimal | int | float | str) -> Decimal:
    return quantize_money(value if isinstance(value, Decimal) else Decimal(str(value)))


@dataclass(frozen=True, slots=True)
class TradeId:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip()
        if not normalized:
            msg = "Trade ID must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TradeId):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return False


class TradeDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class TradeStatus(StrEnum):
    PROPOSED = "proposed"
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TradeReason:
    value: str
    code: str | None = None

    def __post_init__(self) -> None:
        normalized = self.value.strip()
        if not normalized:
            msg = "TradeReason value must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class Confidence:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = _decimal(self.value)
        if not Decimal("0") <= amount <= Decimal("1"):
            msg = "Confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        object.__setattr__(self, "value", amount)


@dataclass(frozen=True, slots=True)
class RewardRisk:
    value: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = _decimal(self.value)
        if amount <= 0:
            msg = "RewardRisk must be greater than zero."
            raise ValueError(msg)
        object.__setattr__(self, "value", amount)


@dataclass(frozen=True, slots=True)
class TradeEvidence:
    data: Mapping[str, float | str | bool] = field(default_factory=dict)
    sources: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        object.__setattr__(self, "sources", tuple(self.sources))

    def __hash__(self) -> int:
        return hash((tuple(sorted(self.data.items())), self.sources))


@dataclass(frozen=True, slots=True)
class Commission:
    amount: Price

    def __post_init__(self) -> None:
        if self.amount.value < 0:
            msg = "Commission must not be negative."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Slippage:
    amount: Price

    def __post_init__(self) -> None:
        if self.amount.value < 0:
            msg = "Slippage must not be negative."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Entry:
    trade_id: TradeId | str
    price: Price
    quantity: Quantity
    entered_at: datetime
    reason: TradeReason | None = None
    evidence: TradeEvidence = field(default_factory=TradeEvidence)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_id", self.trade_id if isinstance(self.trade_id, TradeId) else TradeId(self.trade_id))
        _require_aware(self.entered_at, "Entry entered_at")
        if self.price.value <= 0:
            msg = "Entry price must be greater than zero."
            raise ValueError(msg)
        if self.quantity.value <= 0:
            msg = "Entry quantity must be greater than zero."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Exit:
    trade_id: TradeId | str
    price: Price
    quantity: Quantity
    exited_at: datetime
    reason: TradeReason | None = None
    evidence: TradeEvidence = field(default_factory=TradeEvidence)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_id", self.trade_id if isinstance(self.trade_id, TradeId) else TradeId(self.trade_id))
        _require_aware(self.exited_at, "Exit exited_at")
        if self.price.value <= 0:
            msg = "Exit price must be greater than zero."
            raise ValueError(msg)
        if self.quantity.value <= 0:
            msg = "Exit quantity must be greater than zero."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    trade_id: TradeId | str
    order_id: str
    instrument: Instrument
    side: OrderSide
    quantity: Quantity
    price: Price
    executed_at: datetime
    commission: Commission = field(default_factory=lambda: Commission(Price(0)))
    slippage: Slippage = field(default_factory=lambda: Slippage(Price(0)))

    def __post_init__(self) -> None:
        if not self.execution_id:
            msg = "Execution ID must not be empty."
            raise ValueError(msg)
        if not self.order_id:
            msg = "Order ID must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "trade_id", self.trade_id if isinstance(self.trade_id, TradeId) else TradeId(self.trade_id))
        _require_aware(self.executed_at, "Execution executed_at")

    @property
    def notional(self) -> Decimal:
        return self.quantity.value * self.price.value * Decimal(str(self.instrument.multiplier))


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    confidence: Confidence
    reward_risk: RewardRisk
    expected_return: Price | None = None
    expected_risk: Price | None = None
    evidence: TradeEvidence = field(default_factory=TradeEvidence)


@dataclass(frozen=True, slots=True)
class TradeHealth:
    trade_id: TradeId | str
    score: int
    confidence: Confidence
    reward_risk: RewardRisk
    status: TradeStatus = TradeStatus.PROPOSED
    reason: TradeReason | None = None
    evidence: TradeEvidence = field(default_factory=TradeEvidence)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_id", self.trade_id if isinstance(self.trade_id, TradeId) else TradeId(self.trade_id))
        if not 0 <= self.score <= 100:
            msg = "TradeHealth score must be between 0 and 100."
            raise ValueError(msg)


def _allowed_order_transitions() -> dict[OrderStatus, set[OrderStatus]]:
    return {
        OrderStatus.PENDING: {OrderStatus.SUBMITTED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
        OrderStatus.SUBMITTED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
        OrderStatus.PARTIALLY_FILLED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED},
        OrderStatus.FILLED: set(),
        OrderStatus.CANCELLED: set(),
        OrderStatus.REJECTED: set(),
    }


def _allowed_trade_transitions() -> dict[TradeStatus, set[TradeStatus]]:
    return {
        TradeStatus.PROPOSED: {TradeStatus.OPEN, TradeStatus.CANCELLED, TradeStatus.REJECTED},
        TradeStatus.OPEN: {TradeStatus.PARTIALLY_CLOSED, TradeStatus.CLOSED, TradeStatus.CANCELLED},
        TradeStatus.PARTIALLY_CLOSED: {TradeStatus.PARTIALLY_CLOSED, TradeStatus.CLOSED},
        TradeStatus.CLOSED: set(),
        TradeStatus.CANCELLED: set(),
        TradeStatus.REJECTED: set(),
    }


def _validate_transition(current: Any, new: Any, allowed: Mapping[Any, set[Any]], *, label: str) -> None:
    if new == current:
        return
    allowed_targets = allowed.get(current, set())
    if new not in allowed_targets:
        msg = f"Illegal {label} transition: {current} -> {new}."
        raise TradingDomainError(msg)


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    instrument: Instrument
    side: OrderSide
    order_type: OrderType
    quantity: Quantity
    price: Price | None = None
    stop_price: Price | None = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=_aware_now)
    reason: TradeReason | None = None

    def __post_init__(self) -> None:
        if not self.order_id:
            msg = "Order ID must not be empty."
            raise ValueError(msg)
        _require_aware(self.created_at, "Order created_at")
        if self.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and self.price is None:
            msg = "Limit order must have a valid positive price."
            raise ValueError(msg)
        if self.order_type in {OrderType.STOP, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP} and self.stop_price is None:
            msg = "Stop order must have a valid positive stop price."
            raise ValueError(msg)

    @property
    def notional_value(self) -> Decimal | None:
        if self.price is None:
            return None
        return self.quantity.value * self.price.value * Decimal(str(self.instrument.multiplier))

    def with_status(self, status: OrderStatus) -> Order:
        _validate_transition(self.status, status, _allowed_order_transitions(), label="order")
        return replace(self, status=status)

    def submit(self) -> Order:
        return self.with_status(OrderStatus.SUBMITTED)

    def mark_partially_filled(self) -> Order:
        return self.with_status(OrderStatus.PARTIALLY_FILLED)

    def fill(self) -> Order:
        return self.with_status(OrderStatus.FILLED)

    def cancel(self) -> Order:
        return self.with_status(OrderStatus.CANCELLED)

    def reject(self) -> Order:
        return self.with_status(OrderStatus.REJECTED)


@dataclass(frozen=True, slots=True)
class Trade:
    trade_id: TradeId | str
    order_id: str
    instrument: Instrument
    side: OrderSide
    quantity: Quantity
    price: Price
    executed_at: datetime
    fee: Price = field(default_factory=lambda: Price(0))
    status: TradeStatus = TradeStatus.PROPOSED
    direction: TradeDirection | None = None
    entry: Entry | None = None
    exit: Exit | None = None
    metrics: TradeMetrics | None = None
    health: TradeHealth | None = None
    evidence: TradeEvidence = field(default_factory=TradeEvidence)
    reason: TradeReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_id", self.trade_id if isinstance(self.trade_id, TradeId) else TradeId(self.trade_id))
        if not self.order_id:
            msg = "Order ID must not be empty."
            raise ValueError(msg)
        _require_aware(self.executed_at, "Trade executed_at")
        if self.direction is None:
            object.__setattr__(
                self,
                "direction",
                TradeDirection.LONG if self.side is OrderSide.BUY else TradeDirection.SHORT,
            )
        if self.price.value <= 0:
            msg = "Trade price must be greater than zero."
            raise ValueError(msg)

    @property
    def signed_quantity(self) -> Decimal:
        if self.side is OrderSide.BUY:
            return self.quantity.value
        return -self.quantity.value

    @property
    def gross_notional(self) -> Decimal:
        return self.quantity.value * self.price.value * Decimal(str(self.instrument.multiplier))

    @property
    def cash_flow(self) -> Decimal:
        if self.side is OrderSide.BUY:
            return -self.gross_notional - self.fee.value
        return self.gross_notional - self.fee.value

    @property
    def net_notional(self) -> Decimal:
        return self.gross_notional - self.fee.value

    def with_status(self, status: TradeStatus) -> Trade:
        _validate_transition(self.status, status, _allowed_trade_transitions(), label="trade")
        return replace(self, status=status)

    def open(self, entry: Entry | None = None, *, reason: TradeReason | None = None) -> Trade:
        if entry is not None and entry.trade_id != self.trade_id:
            msg = "Entry trade_id must match trade_id."
            raise TradingDomainError(msg)
        _validate_transition(self.status, TradeStatus.OPEN, _allowed_trade_transitions(), label="trade")
        return replace(
            self,
            status=TradeStatus.OPEN,
            entry=entry or self.entry,
            reason=reason or self.reason,
        )

    def partially_close(self, exit: Exit, *, reason: TradeReason | None = None) -> Trade:
        if exit.trade_id != self.trade_id:
            msg = "Exit trade_id must match trade_id."
            raise TradingDomainError(msg)
        if self.status not in {TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED}:
            msg = f"Illegal trade transition: {self.status} -> {TradeStatus.PARTIALLY_CLOSED}."
            raise TradingDomainError(msg)
        return replace(
            self,
            status=TradeStatus.PARTIALLY_CLOSED,
            exit=exit,
            reason=reason or self.reason,
        )

    def close(self, exit: Exit, *, reason: TradeReason | None = None) -> Trade:
        if exit.trade_id != self.trade_id:
            msg = "Exit trade_id must match trade_id."
            raise TradingDomainError(msg)
        _validate_transition(self.status, TradeStatus.CLOSED, _allowed_trade_transitions(), label="trade")
        return replace(
            self,
            status=TradeStatus.CLOSED,
            exit=exit,
            reason=reason or self.reason,
        )

    def cancel(self, *, reason: TradeReason | None = None) -> Trade:
        _validate_transition(self.status, TradeStatus.CANCELLED, _allowed_trade_transitions(), label="trade")
        return replace(self, status=TradeStatus.CANCELLED, reason=reason or self.reason)

    def reject(self, *, reason: TradeReason | None = None) -> Trade:
        _validate_transition(self.status, TradeStatus.REJECTED, _allowed_trade_transitions(), label="trade")
        return replace(self, status=TradeStatus.REJECTED, reason=reason or self.reason)
