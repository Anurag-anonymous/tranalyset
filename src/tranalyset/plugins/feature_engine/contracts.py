from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TIMEFRAME_CANDLE_CLOSED, TICK_RECEIVED

FEATURES_UPDATED = EventType("FeaturesUpdated")

PUBLISHED_EVENTS = (FEATURES_UPDATED,)
SUBSCRIBED_EVENTS = (TICK_RECEIVED, CANDLE_CLOSED, TIMEFRAME_CANDLE_CLOSED)


def _feature_payload(
    *,
    feature_set_id: str,
    symbol: str,
    exchange: str,
    timeframe: str,
    observed_at: datetime,
    generated_at: datetime,
    source_event_type: str,
) -> dict[str, Any]:
    require_text(feature_set_id, "feature_set_id")
    require_text(symbol, "symbol")
    require_text(exchange, "exchange")
    require_text(timeframe, "timeframe")
    require_text(source_event_type, "source_event_type")
    return {
        "feature_set_id": feature_set_id,
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "observed_at": to_utc_iso(observed_at, field_name="observed_at"),
        "generated_at": to_utc_iso(generated_at, field_name="generated_at"),
        "source_event_type": source_event_type,
    }


@dataclass(frozen=True, slots=True)
class FeatureSet:
    feature_set_id: str
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    source_event_type: str
    features: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    generated_at: datetime | None = None

    def __post_init__(self) -> None:
        require_text(self.feature_set_id, "feature_set_id")
        require_text(self.symbol, "symbol")
        require_text(self.exchange, "exchange")
        require_text(self.timeframe, "timeframe")
        require_text(self.source_event_type, "source_event_type")
        if self.observed_at.tzinfo is None:
            msg = "observed_at must be timezone-aware."
            raise ValueError(msg)
        generated_at = self.generated_at or self.observed_at
        if generated_at.tzinfo is None:
            msg = "generated_at must be timezone-aware."
            raise ValueError(msg)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if not self.features:
            msg = "FeatureSet features must not be empty."
            raise ValueError(msg)

    def with_feature(self, name: str, value: float) -> FeatureSet:
        require_text(name, "name")
        features = dict(self.features)
        features[name] = float(value)
        return type(self)(
            feature_set_id=self.feature_set_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
            features=features,
            metadata=self.metadata,
            generated_at=self.generated_at,
        )

    def to_payload(self) -> dict[str, Any]:
        payload = _feature_payload(
            feature_set_id=self.feature_set_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            generated_at=self.generated_at or self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload["features"] = dict(self.features)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def __hash__(self) -> int:
        return hash(
            (
                self.feature_set_id,
                self.symbol,
                self.exchange,
                self.timeframe,
                self.observed_at,
                self.source_event_type,
                tuple(sorted(self.features.items())),
                tuple(sorted(self.metadata.items())),
                self.generated_at,
            )
        )


@dataclass(frozen=True, slots=True)
class FeaturesUpdatedPayload:
    feature_set_id: str
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    source_event_type: str
    features: Mapping[str, float]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = _feature_payload(
            feature_set_id=self.feature_set_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            generated_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload["features"] = dict(self.features)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


__all__ = [
    "FEATURES_UPDATED",
    "PUBLISHED_EVENTS",
    "SUBSCRIBED_EVENTS",
    "TIMEFRAME_CANDLE_CLOSED",
    "FeatureSet",
    "FeaturesUpdatedPayload",
]
