from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso
from tranalyset.plugins.trade_manager.contracts import TRADE_HEALTH_UPDATED

CONFIDENCE_CHANGED = EventType("ConfidenceChanged")
TRADE_STRENGTH_CHANGED = EventType("TradeStrengthChanged")

TRADE_HEALTH_FACTORS = (
    "trend",
    "momentum",
    "volume",
    "volatility",
    "structure",
    "liquidity",
    "pattern_quality",
    "sector_strength",
    "market_strength",
    "news",
    "sentiment",
    "risk",
)

DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "trend": 1.0,
    "momentum": 1.0,
    "volume": 0.7,
    "volatility": 0.8,
    "structure": 0.9,
    "liquidity": 0.7,
    "pattern_quality": 0.8,
    "sector_strength": 0.6,
    "market_strength": 1.0,
    "news": 0.5,
    "sentiment": 0.6,
    "risk": 1.3,
}


def normalize_weights(raw_weights: Mapping[str, Any] | None) -> dict[str, float]:
    weights = dict(DEFAULT_FACTOR_WEIGHTS)
    for key, value in (raw_weights or {}).items():
        require_text(str(key), "weight key")
        if key not in TRADE_HEALTH_FACTORS:
            msg = f"Unknown trade health factor weight: {key}"
            raise ValueError(msg)
        if not isinstance(value, int | float) or value < 0:
            msg = f"Weight for {key} must be a non-negative number."
            raise ValueError(msg)
        weights[str(key)] = float(value)
    return weights


def _base_payload(
    *,
    trade_id: str,
    symbol: str,
    timeframe: str,
    occurred_at: datetime,
    source_event_type: str | None,
) -> dict[str, Any]:
    require_text(trade_id, "trade_id")
    require_text(symbol, "symbol")
    require_text(timeframe, "timeframe")
    payload: dict[str, Any] = {
        "trade_id": trade_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "occurred_at": to_utc_iso(occurred_at, field_name="occurred_at"),
    }
    optional_text_payload(payload, "source_event_type", source_event_type)
    return payload


@dataclass(frozen=True, slots=True)
class TradeHealthUpdatedPayload:
    trade_id: str
    symbol: str
    timeframe: str
    occurred_at: datetime
    health_score: float
    previous_health_score: float | None
    confidence_lower: float
    confidence_upper: float
    factors: Mapping[str, float] = field(default_factory=dict)
    weights: Mapping[str, float] = field(default_factory=dict)
    explanations: Sequence[str] = ()
    evidence_count: int = 0
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        _validate_score(self.health_score, "health_score")
        _validate_score(self.confidence_lower, "confidence_lower")
        _validate_score(self.confidence_upper, "confidence_upper")
        if self.confidence_lower > self.confidence_upper:
            msg = "confidence_lower must not exceed confidence_upper."
            raise ValueError(msg)
        if self.evidence_count < 0:
            msg = "evidence_count must not be negative."
            raise ValueError(msg)
        if not self.explanations:
            msg = "explanations must not be empty."
            raise ValueError(msg)
        payload = _base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "health_score": self.health_score,
                "confidence_lower": self.confidence_lower,
                "confidence_upper": self.confidence_upper,
                "factors": dict(self.factors),
                "weights": dict(self.weights),
                "explanations": tuple(self.explanations),
                "evidence_count": self.evidence_count,
            }
        )
        if self.previous_health_score is not None:
            _validate_score(self.previous_health_score, "previous_health_score")
            payload["previous_health_score"] = self.previous_health_score
        return payload


@dataclass(frozen=True, slots=True)
class ConfidenceChangedPayload:
    trade_id: str
    symbol: str
    timeframe: str
    occurred_at: datetime
    confidence_lower: float
    confidence_upper: float
    previous_confidence_lower: float | None
    previous_confidence_upper: float | None
    explanations: Sequence[str]
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        _validate_score(self.confidence_lower, "confidence_lower")
        _validate_score(self.confidence_upper, "confidence_upper")
        if self.confidence_lower > self.confidence_upper:
            msg = "confidence_lower must not exceed confidence_upper."
            raise ValueError(msg)
        if not self.explanations:
            msg = "explanations must not be empty."
            raise ValueError(msg)
        payload = _base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["confidence_lower"] = self.confidence_lower
        payload["confidence_upper"] = self.confidence_upper
        payload["explanations"] = tuple(self.explanations)
        if self.previous_confidence_lower is not None:
            _validate_score(self.previous_confidence_lower, "previous_confidence_lower")
            payload["previous_confidence_lower"] = self.previous_confidence_lower
        if self.previous_confidence_upper is not None:
            _validate_score(self.previous_confidence_upper, "previous_confidence_upper")
            payload["previous_confidence_upper"] = self.previous_confidence_upper
        return payload


@dataclass(frozen=True, slots=True)
class TradeStrengthChangedPayload:
    trade_id: str
    symbol: str
    timeframe: str
    occurred_at: datetime
    strength: str
    previous_strength: str | None
    health_score: float
    explanations: Sequence[str]
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.strength, "strength")
        _validate_score(self.health_score, "health_score")
        if not self.explanations:
            msg = "explanations must not be empty."
            raise ValueError(msg)
        payload = _base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["strength"] = self.strength
        payload["health_score"] = self.health_score
        payload["explanations"] = tuple(self.explanations)
        optional_text_payload(payload, "previous_strength", self.previous_strength)
        return payload


def _validate_score(value: float, field_name: str) -> None:
    if not 0.0 <= value <= 100.0:
        msg = f"{field_name} must be between 0 and 100."
        raise ValueError(msg)


PUBLISHED_EVENTS = (
    TRADE_HEALTH_UPDATED,
    CONFIDENCE_CHANGED,
    TRADE_STRENGTH_CHANGED,
)
