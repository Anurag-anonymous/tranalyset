from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_state_engine.contracts import (
    LIQUIDITY_CHANGED,
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
)
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
)
from tranalyset.plugins.news.contracts import MARKET_SENTIMENT_CHANGED, NEWS_ANALYZED
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
)

MARKET_CONTEXT_UPDATED = EventType("MarketContextUpdated")

PUBLISHED_EVENTS = (MARKET_CONTEXT_UPDATED,)

SUBSCRIBED_EVENTS = (
    INDICATORS_UPDATED,
    PATTERN_DETECTED,
    PATTERN_UPDATED,
    PATTERN_INVALIDATED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    NEWS_ANALYZED,
    MARKET_SENTIMENT_CHANGED,
    MARKET_REGIME_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
    LIQUIDITY_CHANGED,
    MOMENTUM_STATE_CHANGED,
    FEATURES_UPDATED,
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
class MarketContextUpdatedPayload:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    trend_context: Mapping[str, Any]
    momentum_context: Mapping[str, Any]
    volatility_context: Mapping[str, Any]
    volume_context: Mapping[str, Any]
    liquidity_context: Mapping[str, Any]
    market_context: Mapping[str, Any]
    sector_context: Mapping[str, Any]
    session_context: Mapping[str, Any]
    risk_context: Mapping[str, Any]
    context_confidence: float
    context_quality: float
    evidence: Mapping[str, float | str | bool]
    source_event_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.context_confidence <= 1.0:
            msg = "context_confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        if not 0.0 <= self.context_quality <= 1.0:
            msg = "context_quality must be between 0.0 and 1.0."
            raise ValueError(msg)
        require_text(self.source_event_type, "source_event_type")

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
                "trend_context": dict(self.trend_context),
                "momentum_context": dict(self.momentum_context),
                "volatility_context": dict(self.volatility_context),
                "volume_context": dict(self.volume_context),
                "liquidity_context": dict(self.liquidity_context),
                "market_context": dict(self.market_context),
                "sector_context": dict(self.sector_context),
                "session_context": dict(self.session_context),
                "risk_context": dict(self.risk_context),
                "context_confidence": self.context_confidence,
                "context_quality": self.context_quality,
                "evidence": dict(self.evidence),
            }
        )
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


__all__ = [
    "MARKET_CONTEXT_UPDATED",
    "PUBLISHED_EVENTS",
    "SUBSCRIBED_EVENTS",
    "MarketContextUpdatedPayload",
]
