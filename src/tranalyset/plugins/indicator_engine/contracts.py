from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import require_text, to_utc_iso
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED

INDICATOR_CALCULATED = EventType("IndicatorCalculated")
INDICATOR_INVALIDATED = EventType("IndicatorInvalidated")

PUBLISHED_EVENTS = (
    INDICATORS_UPDATED,
    INDICATOR_CALCULATED,
    INDICATOR_INVALIDATED,
)

SUBSCRIBED_EVENTS = (
    TICK_RECEIVED,
    CANDLE_CLOSED,
    TIMEFRAME_CANDLE_CLOSED,
    FEATURES_UPDATED,
)


IndicatorValue = float | tuple[float, ...] | None


def _payload_base(
    *,
    symbol: str,
    exchange: str,
    timeframe: str,
    observed_at: datetime,
    source_event_type: str,
) -> dict[str, Any]:
    require_text(symbol, "symbol")
    require_text(exchange, "exchange")
    require_text(timeframe, "timeframe")
    require_text(source_event_type, "source_event_type")
    return {
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "observed_at": to_utc_iso(observed_at, field_name="observed_at"),
        "source_event_type": source_event_type,
    }


@dataclass(frozen=True, slots=True)
class IndicatorCalculatedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    indicator: str
    value: IndicatorValue
    version: str
    source_event_type: str
    dependencies: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        require_text(self.indicator, "indicator")
        payload = _payload_base(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload["indicator"] = self.indicator
        payload["value"] = self.value
        payload["version"] = self.version
        payload["dependencies"] = tuple(self.dependencies)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class IndicatorInvalidatedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    indicator: str
    reason: str
    source_event_type: str
    previous_version: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        require_text(self.indicator, "indicator")
        require_text(self.reason, "reason")
        payload = _payload_base(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload["indicator"] = self.indicator
        payload["reason"] = self.reason
        if self.previous_version is not None:
            require_text(self.previous_version, "previous_version")
            payload["previous_version"] = self.previous_version
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class IndicatorsUpdatedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    indicators: Mapping[str, IndicatorValue]
    versions: Mapping[str, str]
    source_event_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = _payload_base(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload["indicators"] = dict(self.indicators)
        payload["versions"] = dict(self.versions)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


__all__ = [
    "INDICATOR_CALCULATED",
    "INDICATOR_INVALIDATED",
    "INDICATORS_UPDATED",
    "PUBLISHED_EVENTS",
    "SUBSCRIBED_EVENTS",
    "IndicatorCalculatedPayload",
    "IndicatorInvalidatedPayload",
    "IndicatorsUpdatedPayload",
    "IndicatorValue",
]
