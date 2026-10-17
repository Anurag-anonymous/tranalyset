from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED

PATTERN_DETECTED = EventType("PatternDetected")
PATTERN_UPDATED = EventType("PatternUpdated")
PATTERN_INVALIDATED = EventType("PatternInvalidated")

PUBLISHED_EVENTS = (
    PATTERN_DETECTED,
    PATTERN_UPDATED,
    PATTERN_INVALIDATED,
)

SUBSCRIBED_EVENTS = (
    CANDLE_CLOSED,
    TIMEFRAME_CANDLE_CLOSED,
    INDICATORS_UPDATED,
    FEATURES_UPDATED,
    MARKET_REGIME_CHANGED,
)


def _base_payload(
    *,
    symbol: str,
    exchange: str,
    timeframe: str,
    observed_at: datetime,
    pattern_id: str,
    pattern: str,
    category: str,
    source_event_type: str,
) -> dict[str, Any]:
    require_text(symbol, "symbol")
    require_text(exchange, "exchange")
    require_text(timeframe, "timeframe")
    require_text(pattern_id, "pattern_id")
    require_text(pattern, "pattern")
    require_text(category, "category")
    require_text(source_event_type, "source_event_type")
    return {
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "observed_at": to_utc_iso(observed_at, field_name="observed_at"),
        "pattern_id": pattern_id,
        "pattern": pattern,
        "category": category,
        "source_event_type": source_event_type,
    }


@dataclass(frozen=True, slots=True)
class PatternBasePayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    pattern_id: str
    pattern: str
    category: str
    confidence: float
    strength: float
    age_bars: int
    validity: bool
    historical_success_rate: float
    reliability: float
    current_context: Mapping[str, float | str | bool]
    volume_confirmation: float
    trend_confirmation: float
    volatility: float
    market_regime: str | None
    evidence: Mapping[str, float | str | bool]
    source_event_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        if not 0.0 <= self.historical_success_rate <= 1.0:
            msg = "historical_success_rate must be between 0.0 and 1.0."
            raise ValueError(msg)
        if not 0.0 <= self.reliability <= 1.0:
            msg = "reliability must be between 0.0 and 1.0."
            raise ValueError(msg)
        if self.strength < 0.0:
            msg = "strength must not be negative."
            raise ValueError(msg)
        if self.age_bars < 0:
            msg = "age_bars must not be negative."
            raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            pattern_id=self.pattern_id,
            pattern=self.pattern,
            category=self.category,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "confidence": self.confidence,
                "strength": self.strength,
                "age_bars": self.age_bars,
                "validity": self.validity,
                "historical_success_rate": self.historical_success_rate,
                "reliability": self.reliability,
                "current_context": dict(self.current_context),
                "volume_confirmation": self.volume_confirmation,
                "trend_confirmation": self.trend_confirmation,
                "volatility": self.volatility,
                "evidence": dict(self.evidence),
            }
        )
        optional_text_payload(payload, "market_regime", self.market_regime)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class PatternDetectedPayload(PatternBasePayload):
    pass


@dataclass(frozen=True, slots=True)
class PatternUpdatedPayload(PatternBasePayload):
    previous_confidence: float | None = None
    previous_strength: float | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = PatternBasePayload.to_payload(self)
        if self.previous_confidence is not None:
            payload["previous_confidence"] = self.previous_confidence
        if self.previous_strength is not None:
            payload["previous_strength"] = self.previous_strength
        return payload


@dataclass(frozen=True, slots=True)
class PatternInvalidatedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    pattern_id: str
    pattern: str
    category: str
    reason: str
    confidence: float
    strength: float
    age_bars: int
    historical_success_rate: float
    reliability: float
    current_context: Mapping[str, float | str | bool]
    volume_confirmation: float
    trend_confirmation: float
    volatility: float
    market_regime: str | None
    evidence: Mapping[str, float | str | bool]
    source_event_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        require_text(self.reason, "reason")
        payload = _base_payload(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            pattern_id=self.pattern_id,
            pattern=self.pattern,
            category=self.category,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "reason": self.reason,
                "confidence": self.confidence,
                "strength": self.strength,
                "age_bars": self.age_bars,
                "historical_success_rate": self.historical_success_rate,
                "reliability": self.reliability,
                "current_context": dict(self.current_context),
                "volume_confirmation": self.volume_confirmation,
                "trend_confirmation": self.trend_confirmation,
                "volatility": self.volatility,
                "evidence": dict(self.evidence),
            }
        )
        optional_text_payload(payload, "market_regime", self.market_regime)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


__all__ = [
    "PATTERN_DETECTED",
    "PATTERN_INVALIDATED",
    "PATTERN_UPDATED",
    "PUBLISHED_EVENTS",
    "SUBSCRIBED_EVENTS",
    "PatternDetectedPayload",
    "PatternInvalidatedPayload",
    "PatternUpdatedPayload",
]
