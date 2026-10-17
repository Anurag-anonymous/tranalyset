from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

INDICATORS_UPDATED = EventType("IndicatorsUpdated")
TREND_CHANGED = EventType("TrendChanged")
MOMENTUM_CHANGED = EventType("MomentumChanged")
STRUCTURE_CHANGED = EventType("StructureChanged")


def _base_payload(
    *,
    symbol: str,
    timeframe: str,
    occurred_at: datetime,
    source_event_type: str | None,
) -> dict[str, Any]:
    require_text(symbol, "symbol")
    require_text(timeframe, "timeframe")
    payload: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "occurred_at": to_utc_iso(occurred_at, field_name="occurred_at"),
    }
    optional_text_payload(payload, "source_event_type", source_event_type)
    return payload


@dataclass(frozen=True, slots=True)
class IndicatorsUpdatedPayload:
    symbol: str
    timeframe: str
    occurred_at: datetime
    indicators: Mapping[str, float]
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if not self.indicators:
            msg = "indicators must not be empty."
            raise ValueError(msg)
        payload = _base_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["indicators"] = dict(self.indicators)
        return payload


@dataclass(frozen=True, slots=True)
class TrendChangedPayload:
    symbol: str
    timeframe: str
    occurred_at: datetime
    trend: str
    previous_trend: str | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.trend, "trend")
        payload = _base_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["trend"] = self.trend
        optional_text_payload(payload, "previous_trend", self.previous_trend)
        return payload


@dataclass(frozen=True, slots=True)
class MomentumChangedPayload:
    symbol: str
    timeframe: str
    occurred_at: datetime
    momentum: str
    previous_momentum: str | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.momentum, "momentum")
        payload = _base_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["momentum"] = self.momentum
        optional_text_payload(payload, "previous_momentum", self.previous_momentum)
        return payload


@dataclass(frozen=True, slots=True)
class StructureChangedPayload:
    symbol: str
    timeframe: str
    occurred_at: datetime
    structure: str
    previous_structure: str | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.structure, "structure")
        payload = _base_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["structure"] = self.structure
        optional_text_payload(payload, "previous_structure", self.previous_structure)
        return payload


PUBLISHED_EVENTS = (
    INDICATORS_UPDATED,
    TREND_CHANGED,
    MOMENTUM_CHANGED,
    STRUCTURE_CHANGED,
)
