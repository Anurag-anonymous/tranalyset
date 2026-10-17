from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

TICK_RECEIVED = EventType("TickReceived")
CANDLE_CLOSED = EventType("CandleClosed")
TIMEFRAME_CANDLE_CLOSED = EventType("TimeframeCandleClosed")
MARKET_OPENED = EventType("MarketOpened")
MARKET_CLOSED = EventType("MarketClosed")
CONNECTION_LOST = EventType("ConnectionLost")
CONNECTION_RESTORED = EventType("ConnectionRestored")


@dataclass(frozen=True, slots=True)
class TickReceivedPayload:
    symbol: str
    price: float
    size: float
    received_at: datetime
    venue: str | None = None
    side: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.symbol, "symbol")
        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "price": self.price,
            "size": self.size,
            "received_at": to_utc_iso(self.received_at, field_name="received_at"),
        }
        optional_text_payload(payload, "venue", self.venue)
        optional_text_payload(payload, "side", self.side)
        return payload


@dataclass(frozen=True, slots=True)
class CandleClosedPayload:
    symbol: str
    timeframe: str
    opened_at: datetime
    closed_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    venue: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.symbol, "symbol")
        require_text(self.timeframe, "timeframe")
        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "opened_at": to_utc_iso(self.opened_at, field_name="opened_at"),
            "closed_at": to_utc_iso(self.closed_at, field_name="closed_at"),
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
            "volume": self.volume,
        }
        optional_text_payload(payload, "venue", self.venue)
        return payload


@dataclass(frozen=True, slots=True)
class MarketSessionPayload:
    market: str
    occurred_at: datetime
    venue: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.market, "market")
        payload: dict[str, Any] = {
            "market": self.market,
            "occurred_at": to_utc_iso(self.occurred_at, field_name="occurred_at"),
        }
        optional_text_payload(payload, "venue", self.venue)
        return payload


@dataclass(frozen=True, slots=True)
class ConnectionStatePayload:
    connection_name: str
    occurred_at: datetime
    reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.connection_name, "connection_name")
        payload: dict[str, Any] = {
            "connection_name": self.connection_name,
            "occurred_at": to_utc_iso(self.occurred_at, field_name="occurred_at"),
        }
        optional_text_payload(payload, "reason", self.reason)
        return payload


PUBLISHED_EVENTS = (
    TICK_RECEIVED,
    CANDLE_CLOSED,
    TIMEFRAME_CANDLE_CLOSED,
    MARKET_OPENED,
    MARKET_CLOSED,
    CONNECTION_LOST,
    CONNECTION_RESTORED,
)
