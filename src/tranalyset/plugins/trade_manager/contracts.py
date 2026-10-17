from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso
from tranalyset.plugins.risk.contracts import TRAILING_STOP_UPDATED

TRADE_SIGNAL_GENERATED = EventType("TradeSignalGenerated")
TRADE_HEALTH_UPDATED = EventType("TradeHealthUpdated")

TRADE_OPENED = EventType("TradeOpened")
TRADE_MODIFIED = EventType("TradeModified")
TRADE_CLOSED = EventType("TradeClosed")


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
class TradeOpenedPayload:
    trade_id: str
    symbol: str
    opened_at: datetime
    quantity: float
    entry_price: float | None = None
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None
    strategy_id: str | None = None
    side: str = "long"

    def to_payload(self) -> dict[str, Any]:
        if self.quantity <= 0:
            msg = "quantity must be greater than zero."
            raise ValueError(msg)
        if self.side.lower() != "long":
            raise ValueError("The paper execution engine currently supports long trades only.")
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.opened_at,
            source_event_type=self.source_event_type,
        )
        payload["quantity"] = self.quantity
        if self.entry_price is not None:
            if self.entry_price <= 0:
                msg = "entry_price must be greater than zero."
                raise ValueError(msg)
            payload["entry_price"] = self.entry_price
        payload["evidence"] = dict(self.evidence)
        optional_text_payload(payload, "strategy_id", self.strategy_id)
        payload["side"] = self.side.lower()
        return payload


@dataclass(frozen=True, slots=True)
class TradeModifiedPayload:
    trade_id: str
    symbol: str
    modified_at: datetime
    changes: Mapping[str, float | str | bool]
    reason: str | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if not self.changes:
            msg = "changes must not be empty."
            raise ValueError(msg)
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.modified_at,
            source_event_type=self.source_event_type,
        )
        payload["changes"] = dict(self.changes)
        optional_text_payload(payload, "reason", self.reason)
        return payload


@dataclass(frozen=True, slots=True)
class TradeClosedPayload:
    trade_id: str
    symbol: str
    closed_at: datetime
    reason: str
    exit_price: float | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.reason, "reason")
        payload = _trade_base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.closed_at,
            source_event_type=self.source_event_type,
        )
        payload["reason"] = self.reason
        if self.exit_price is not None:
            if self.exit_price <= 0:
                msg = "exit_price must be greater than zero."
                raise ValueError(msg)
            payload["exit_price"] = self.exit_price
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
    TRADE_OPENED,
    TRADE_MODIFIED,
    TRADE_CLOSED,
)
