from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import require_text, to_utc_iso
from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
)
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
)
from tranalyset.plugins.risk.contracts import (
    POSITION_SIZE_CALCULATED,
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
)
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED

PROBABILITY_UPDATED = EventType("ProbabilityUpdated")
CONFIDENCE_UPDATED = EventType("ConfidenceUpdated")

PUBLISHED_EVENTS = (
    PROBABILITY_UPDATED,
    CONFIDENCE_UPDATED,
)

SUBSCRIBED_EVENTS = (
    MARKET_CONTEXT_UPDATED,
    TRADE_HEALTH_UPDATED,
    PATTERN_DETECTED,
    PATTERN_UPDATED,
    PATTERN_INVALIDATED,
    INDICATORS_UPDATED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    RISK_APPROVED,
    RISK_REJECTED,
    POSITION_SIZE_CALCULATED,
    STOP_LOSS_UPDATED,
)


def _base_payload(
    *,
    trade_id: str,
    symbol: str,
    exchange: str,
    timeframe: str,
    observed_at: datetime,
    source_event_type: str,
) -> dict[str, Any]:
    require_text(trade_id, "trade_id")
    require_text(symbol, "symbol")
    require_text(exchange, "exchange")
    require_text(timeframe, "timeframe")
    require_text(source_event_type, "source_event_type")
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "observed_at": to_utc_iso(observed_at, field_name="observed_at"),
        "source_event_type": source_event_type,
    }


@dataclass(frozen=True, slots=True)
class ProbabilityUpdatedPayload:
    trade_id: str
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    bullish_probability: float
    bearish_probability: float
    trend_continuation_probability: float
    trend_reversal_probability: float
    breakout_probability: float
    breakdown_probability: float
    probability_distribution: Mapping[str, float]
    confidence_lower: float
    confidence_upper: float
    reasoning_metadata: Mapping[str, Any] = field(default_factory=dict)
    source_event_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_probability(self.bullish_probability, "bullish_probability")
        _validate_probability(self.bearish_probability, "bearish_probability")
        _validate_probability(self.trend_continuation_probability, "trend_continuation_probability")
        _validate_probability(self.trend_reversal_probability, "trend_reversal_probability")
        _validate_probability(self.breakout_probability, "breakout_probability")
        _validate_probability(self.breakdown_probability, "breakdown_probability")
        _validate_probability(self.confidence_lower, "confidence_lower")
        _validate_probability(self.confidence_upper, "confidence_upper")
        if self.confidence_lower > self.confidence_upper:
            msg = "confidence_lower must not exceed confidence_upper."
            raise ValueError(msg)
        _validate_distribution(self.probability_distribution)
        require_text(self.source_event_type, "source_event_type")

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "bullish_probability": self.bullish_probability,
                "bearish_probability": self.bearish_probability,
                "trend_continuation_probability": self.trend_continuation_probability,
                "trend_reversal_probability": self.trend_reversal_probability,
                "breakout_probability": self.breakout_probability,
                "breakdown_probability": self.breakdown_probability,
                "probability_distribution": dict(self.probability_distribution),
                "confidence_lower": self.confidence_lower,
                "confidence_upper": self.confidence_upper,
                "reasoning_metadata": dict(self.reasoning_metadata),
            }
        )
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class ConfidenceUpdatedPayload:
    trade_id: str
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    confidence: float
    previous_confidence: float | None
    confidence_lower: float
    confidence_upper: float
    reasoning_metadata: Mapping[str, Any] = field(default_factory=dict)
    source_event_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_probability(self.confidence, "confidence")
        _validate_probability(self.confidence_lower, "confidence_lower")
        _validate_probability(self.confidence_upper, "confidence_upper")
        if self.confidence_lower > self.confidence_upper:
            msg = "confidence_lower must not exceed confidence_upper."
            raise ValueError(msg)
        if self.previous_confidence is not None:
            _validate_probability(self.previous_confidence, "previous_confidence")
        require_text(self.source_event_type, "source_event_type")

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "confidence": self.confidence,
                "confidence_lower": self.confidence_lower,
                "confidence_upper": self.confidence_upper,
                "reasoning_metadata": dict(self.reasoning_metadata),
            }
        )
        if self.previous_confidence is not None:
            payload["previous_confidence"] = self.previous_confidence
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def _validate_probability(value: float, field_name: str) -> None:
    if not 0.0 <= value <= 1.0:
        msg = f"{field_name} must be between 0.0 and 1.0."
        raise ValueError(msg)


def _validate_distribution(distribution: Mapping[str, float]) -> None:
    if not distribution:
        msg = "probability_distribution must not be empty."
        raise ValueError(msg)
    total = 0.0
    for name, value in distribution.items():
        require_text(name, "probability_distribution key")
        _validate_probability(value, f"probability_distribution[{name}]")
        total += value
    if total <= 0.0:
        msg = "probability_distribution must sum to a positive value."
        raise ValueError(msg)


__all__ = [
    "CONFIDENCE_UPDATED",
    "PUBLISHED_EVENTS",
    "PROBABILITY_UPDATED",
    "ProbabilityUpdatedPayload",
    "ConfidenceUpdatedPayload",
    "SUBSCRIBED_EVENTS",
]
