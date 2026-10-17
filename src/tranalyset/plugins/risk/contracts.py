from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

TRADE_REQUESTED = EventType("TradeRequested")
TRADE_UPDATED = EventType("TradeUpdated")
PORTFOLIO_UPDATED = EventType("PortfolioUpdated")

RISK_APPROVED = EventType("RiskApproved")
RISK_REJECTED = EventType("RiskRejected")
POSITION_SIZE_CALCULATED = EventType("PositionSizeCalculated")
STOP_LOSS_UPDATED = EventType("StopLossUpdated")
TAKE_PROFIT_UPDATED = EventType("TakeProfitUpdated")
TRAILING_STOP_UPDATED = EventType("TrailingStopUpdated")


def _trade_base_payload(
    *,
    trade_id: str,
    symbol: str,
    occurred_at: datetime,
    source_event_type: str | None,
) -> dict[str, Any]:
    require_text(trade_id, "trade_id")
    require_text(symbol, "symbol")
    payload: dict[str, Any] = {
        "trade_id": trade_id,
        "symbol": symbol,
        "occurred_at": to_utc_iso(occurred_at, field_name="occurred_at"),
    }
    optional_text_payload(payload, "source_event_type", source_event_type)
    return payload


@dataclass(frozen=True, slots=True)
class RiskApprovedPayload:
    trade_id: str
    symbol: str
    approved_at: datetime
    reason: str
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None
    entry_price: float | None = None
    quantity: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    trailing_stop_price: float | None = None
    strategy_id: str | None = None
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    sector: str | None = None
    correlation_group: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.reason, "reason")
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.approved_at,
            source_event_type=self.source_event_type,
        )
        payload["reason"] = self.reason
        payload["evidence"] = dict(self.evidence)
        for name, value in (
            ("entry_price", self.entry_price),
            ("quantity", self.quantity),
            ("stop_loss_price", self.stop_loss_price),
            ("take_profit_price", self.take_profit_price),
            ("trailing_stop_price", self.trailing_stop_price),
            ("limit_price", self.limit_price),
            ("stop_price", self.stop_price),
        ):
            if value is not None:
                if value <= 0:
                    raise ValueError(f"{name} must be positive.")
                payload[name] = value
        if self.order_type not in {"market", "limit", "stop"}:
            raise ValueError("order_type must be market, limit, or stop.")
        payload["order_type"] = self.order_type
        optional_text_payload(payload, "strategy_id", self.strategy_id)
        optional_text_payload(payload, "sector", self.sector)
        optional_text_payload(payload, "correlation_group", self.correlation_group)
        return payload


@dataclass(frozen=True, slots=True)
class RiskRejectedPayload:
    trade_id: str
    symbol: str
    rejected_at: datetime
    reason: str
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.reason, "reason")
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.rejected_at,
            source_event_type=self.source_event_type,
        )
        payload["reason"] = self.reason
        payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class TradeRequestedPayload:
    trade_id: str
    signal_id: str
    strategy_id: str
    symbol: str
    timeframe: str
    requested_at: datetime
    entry_price: float
    confidence: float
    reward_risk: float
    side: str = "long"
    supporting_evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None
    order_type: str = "market"
    sector: str | None = None
    correlation_group: str | None = None

    def to_payload(self) -> dict[str, Any]:
        for field_name, value in (
            ("signal_id", self.signal_id),
            ("strategy_id", self.strategy_id),
            ("timeframe", self.timeframe),
        ):
            require_text(value, field_name)
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one.")
        if self.reward_risk <= 0:
            raise ValueError("reward_risk must be positive.")
        if self.side.lower() != "long":
            raise ValueError("Paper trade requests currently support long positions only.")
        if self.order_type not in {"market", "limit", "stop"}:
            raise ValueError("order_type must be market, limit, or stop.")
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.requested_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "signal_id": self.signal_id,
                "strategy_id": self.strategy_id,
                "timeframe": self.timeframe,
                "entry_price": self.entry_price,
                "confidence": self.confidence,
                "reward_risk": self.reward_risk,
                "side": self.side.lower(),
                "order_type": self.order_type,
                "supporting_evidence": dict(self.supporting_evidence),
            }
        )
        optional_text_payload(payload, "sector", self.sector)
        optional_text_payload(payload, "correlation_group", self.correlation_group)
        return payload


@dataclass(frozen=True, slots=True)
class PositionSizeCalculatedPayload:
    trade_id: str
    symbol: str
    calculated_at: datetime
    quantity: float
    notional_value: float | None = None
    risk_amount: float | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if self.quantity <= 0:
            msg = "quantity must be greater than zero."
            raise ValueError(msg)
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.calculated_at,
            source_event_type=self.source_event_type,
        )
        payload["quantity"] = self.quantity
        if self.notional_value is not None:
            if self.notional_value < 0:
                msg = "notional_value must not be negative."
                raise ValueError(msg)
            payload["notional_value"] = self.notional_value
        if self.risk_amount is not None:
            if self.risk_amount < 0:
                msg = "risk_amount must not be negative."
                raise ValueError(msg)
            payload["risk_amount"] = self.risk_amount
        return payload


@dataclass(frozen=True, slots=True)
class StopLossUpdatedPayload:
    trade_id: str
    symbol: str
    updated_at: datetime
    stop_loss_price: float
    previous_stop_loss_price: float | None = None
    reason: str | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if self.stop_loss_price <= 0:
            msg = "stop_loss_price must be greater than zero."
            raise ValueError(msg)
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.updated_at,
            source_event_type=self.source_event_type,
        )
        payload["stop_loss_price"] = self.stop_loss_price
        if self.previous_stop_loss_price is not None:
            if self.previous_stop_loss_price <= 0:
                msg = "previous_stop_loss_price must be greater than zero."
                raise ValueError(msg)
            payload["previous_stop_loss_price"] = self.previous_stop_loss_price
        optional_text_payload(payload, "reason", self.reason)
        return payload


TAKE_PROFIT_UPDATED = EventType("TakeProfitUpdated")


@dataclass(frozen=True, slots=True)
class TakeProfitUpdatedPayload:
    trade_id: str
    symbol: str
    updated_at: datetime
    take_profit_price: float
    previous_take_profit_price: float | None = None
    reason: str | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if self.take_profit_price <= 0:
            msg = "take_profit_price must be greater than zero."
            raise ValueError(msg)
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.updated_at,
            source_event_type=self.source_event_type,
        )
        payload["take_profit_price"] = self.take_profit_price
        if self.previous_take_profit_price is not None:
            if self.previous_take_profit_price <= 0:
                msg = "previous_take_profit_price must be greater than zero."
                raise ValueError(msg)
            payload["previous_take_profit_price"] = self.previous_take_profit_price
        optional_text_payload(payload, "reason", self.reason)
        return payload


@dataclass(frozen=True, slots=True)
class TrailingStopUpdatedPayload:
    trade_id: str
    symbol: str
    updated_at: datetime
    trailing_stop_price: float
    previous_trailing_stop_price: float | None = None
    reason: str | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if self.trailing_stop_price <= 0:
            msg = "trailing_stop_price must be greater than zero."
            raise ValueError(msg)
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.updated_at,
            source_event_type=self.source_event_type,
        )
        payload["trailing_stop_price"] = self.trailing_stop_price
        if self.previous_trailing_stop_price is not None:
            if self.previous_trailing_stop_price <= 0:
                msg = "previous_trailing_stop_price must be greater than zero."
                raise ValueError(msg)
            payload["previous_trailing_stop_price"] = self.previous_trailing_stop_price
        optional_text_payload(payload, "reason", self.reason)
        return payload


PUBLISHED_EVENTS = (
    RISK_APPROVED,
    RISK_REJECTED,
    POSITION_SIZE_CALCULATED,
    STOP_LOSS_UPDATED,
    TAKE_PROFIT_UPDATED,
    TRAILING_STOP_UPDATED,
)
