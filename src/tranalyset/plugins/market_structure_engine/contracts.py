from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
)

MARKET_STRUCTURE_UPDATED = EventType("MarketStructureUpdated")
STRUCTURE_BREAK = EventType("StructureBreak")
CHARACTER_CHANGED = EventType("CharacterChanged")
LIQUIDITY_DETECTED = EventType("LiquidityDetected")

PUBLISHED_EVENTS = (
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
)

SUBSCRIBED_EVENTS = (
    CANDLE_CLOSED,
    TIMEFRAME_CANDLE_CLOSED,
    INDICATORS_UPDATED,
    FEATURES_UPDATED,
    PATTERN_DETECTED,
    PATTERN_UPDATED,
    PATTERN_INVALIDATED,
    MARKET_REGIME_CHANGED,
)


def _base_payload(
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
class MarketStructureUpdatedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    structure: str
    internal_structure: str
    external_structure: str
    confidence: float
    swing_high: float | None = None
    swing_low: float | None = None
    higher_highs: int = 0
    higher_lows: int = 0
    lower_highs: int = 0
    lower_lows: int = 0
    equal_highs: int = 0
    equal_lows: int = 0
    break_of_structure: bool = False
    character_change: bool = False
    liquidity_zone: str | None = None
    premium_zone: bool = False
    discount_zone: bool = False
    market_regime: str | None = None
    detected: Sequence[str] = field(default_factory=tuple)
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.structure, "structure")
        require_text(self.internal_structure, "internal_structure")
        require_text(self.external_structure, "external_structure")
        require_text(self.source_event_type, "source_event_type")
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        for field_name in (
            "higher_highs",
            "higher_lows",
            "lower_highs",
            "lower_lows",
            "equal_highs",
            "equal_lows",
        ):
            if getattr(self, field_name) < 0:
                msg = f"{field_name} must not be negative."
                raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "structure": self.structure,
                "internal_structure": self.internal_structure,
                "external_structure": self.external_structure,
                "confidence": self.confidence,
                "swing_high": self.swing_high,
                "swing_low": self.swing_low,
                "higher_highs": self.higher_highs,
                "higher_lows": self.higher_lows,
                "lower_highs": self.lower_highs,
                "lower_lows": self.lower_lows,
                "equal_highs": self.equal_highs,
                "equal_lows": self.equal_lows,
                "break_of_structure": self.break_of_structure,
                "character_change": self.character_change,
                "liquidity_zone": self.liquidity_zone,
                "premium_zone": self.premium_zone,
                "discount_zone": self.discount_zone,
                "detected": tuple(self.detected),
                "evidence": dict(self.evidence),
            }
        )
        optional_text_payload(payload, "market_regime", self.market_regime)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class StructureBreakPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    break_type: str
    direction: str
    level: float
    confidence: float
    previous_structure: str | None = None
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.break_type, "break_type")
        require_text(self.direction, "direction")
        require_text(self.source_event_type, "source_event_type")
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "break_type": self.break_type,
                "direction": self.direction,
                "level": self.level,
                "confidence": self.confidence,
                "evidence": dict(self.evidence),
            }
        )
        optional_text_payload(payload, "previous_structure", self.previous_structure)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class CharacterChangedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    previous_character: str | None
    character: str
    confidence: float
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.character, "character")
        require_text(self.source_event_type, "source_event_type")
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "character": self.character,
                "confidence": self.confidence,
                "evidence": dict(self.evidence),
            }
        )
        optional_text_payload(payload, "previous_character", self.previous_character)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class LiquidityDetectedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    liquidity_type: str
    side: str
    level: float
    confidence: float
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.liquidity_type, "liquidity_type")
        require_text(self.side, "side")
        require_text(self.source_event_type, "source_event_type")
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "liquidity_type": self.liquidity_type,
                "side": self.side,
                "level": self.level,
                "confidence": self.confidence,
                "evidence": dict(self.evidence),
            }
        )
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


__all__ = [
    "CHARACTER_CHANGED",
    "LIQUIDITY_DETECTED",
    "MARKET_STRUCTURE_UPDATED",
    "PUBLISHED_EVENTS",
    "STRUCTURE_BREAK",
    "SUBSCRIBED_EVENTS",
    "CharacterChangedPayload",
    "LiquidityDetectedPayload",
    "MarketStructureUpdatedPayload",
    "StructureBreakPayload",
]
