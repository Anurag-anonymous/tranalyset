from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

PATTERN_DETECTED = EventType("PatternDetected")
PATTERN_INVALIDATED = EventType("PatternInvalidated")


def _base_payload(
    *,
    symbol: str,
    timeframe: str,
    pattern: str,
    occurred_at: datetime,
    source_event_type: str | None,
) -> dict[str, Any]:
    require_text(symbol, "symbol")
    require_text(timeframe, "timeframe")
    require_text(pattern, "pattern")
    payload: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "pattern": pattern,
        "occurred_at": to_utc_iso(occurred_at, field_name="occurred_at"),
    }
    optional_text_payload(payload, "source_event_type", source_event_type)
    return payload


@dataclass(frozen=True, slots=True)
class PatternDetectedPayload:
    symbol: str
    timeframe: str
    pattern: str
    occurred_at: datetime
    confidence: float
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        payload = _base_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            pattern=self.pattern,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["confidence"] = self.confidence
        payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class PatternInvalidatedPayload:
    symbol: str
    timeframe: str
    pattern: str
    occurred_at: datetime
    reason: str
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.reason, "reason")
        payload = _base_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            pattern=self.pattern,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["reason"] = self.reason
        return payload


PUBLISHED_EVENTS = (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
)
