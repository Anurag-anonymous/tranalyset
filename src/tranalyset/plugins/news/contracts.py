from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

NEWS_RECEIVED = EventType("NewsReceived")
NEWS_ANALYZED = EventType("NewsAnalyzed")
MARKET_SENTIMENT_CHANGED = EventType("MarketSentimentChanged")


@dataclass(frozen=True, slots=True)
class NewsReceivedPayload:
    headline: str
    source_name: str
    received_at: datetime
    url: str | None = None
    symbols: tuple[str, ...] = ()
    summary: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.headline, "headline")
        require_text(self.source_name, "source_name")
        payload: dict[str, Any] = {
            "headline": self.headline,
            "source_name": self.source_name,
            "received_at": to_utc_iso(self.received_at, field_name="received_at"),
            "symbols": tuple(self.symbols),
        }
        optional_text_payload(payload, "url", self.url)
        optional_text_payload(payload, "summary", self.summary)
        return payload


@dataclass(frozen=True, slots=True)
class NewsAnalyzedPayload:
    headline: str
    analyzed_at: datetime
    sentiment: str
    confidence: float
    symbols: tuple[str, ...] = ()
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.headline, "headline")
        require_text(self.sentiment, "sentiment")
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "headline": self.headline,
            "analyzed_at": to_utc_iso(self.analyzed_at, field_name="analyzed_at"),
            "sentiment": self.sentiment,
            "confidence": self.confidence,
            "symbols": tuple(self.symbols),
            "evidence": dict(self.evidence),
        }
        optional_text_payload(payload, "source_event_type", self.source_event_type)
        return payload


@dataclass(frozen=True, slots=True)
class MarketSentimentChangedPayload:
    market: str
    sentiment: str
    previous_sentiment: str | None
    occurred_at: datetime
    confidence: float
    evidence_count: int
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.market, "market")
        require_text(self.sentiment, "sentiment")
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        if self.evidence_count < 0:
            msg = "evidence_count must not be negative."
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "market": self.market,
            "sentiment": self.sentiment,
            "occurred_at": to_utc_iso(self.occurred_at, field_name="occurred_at"),
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
        }
        optional_text_payload(payload, "previous_sentiment", self.previous_sentiment)
        optional_text_payload(payload, "source_event_type", self.source_event_type)
        return payload


PUBLISHED_EVENTS = (
    NEWS_RECEIVED,
    NEWS_ANALYZED,
    MARKET_SENTIMENT_CHANGED,
)
