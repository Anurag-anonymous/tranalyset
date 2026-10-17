from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

REASONING_GENERATED = EventType("ReasoningGenerated")

REASONING_CATEGORIES = (
    "trade_health",
    "risk",
    "entry_candidate",
    "exit_candidate",
    "trailing_stop",
    "market_regime",
    "pattern_detection",
)


@dataclass(frozen=True, slots=True)
class ReasoningGeneratedPayload:
    reasoning_id: str
    generated_at: datetime
    category: str
    source_event_type: str
    source_event_timestamp: datetime
    subject: Mapping[str, str]
    summary: str
    explanation: Sequence[str]
    evidence: Mapping[str, Any]
    limitations: Sequence[str] = field(default_factory=tuple)
    provider: str = "deterministic"
    model: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.reasoning_id, "reasoning_id")
        require_text(self.category, "category")
        require_text(self.source_event_type, "source_event_type")
        require_text(self.summary, "summary")
        require_text(self.provider, "provider")
        if self.category not in REASONING_CATEGORIES:
            msg = f"Unsupported reasoning category: {self.category}"
            raise ValueError(msg)
        if not self.subject:
            msg = "subject must not be empty."
            raise ValueError(msg)
        if not self.explanation:
            msg = "explanation must not be empty."
            raise ValueError(msg)
        if not self.evidence:
            msg = "evidence must not be empty."
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "reasoning_id": self.reasoning_id,
            "generated_at": to_utc_iso(self.generated_at, field_name="generated_at"),
            "category": self.category,
            "source_event_type": self.source_event_type,
            "source_event_timestamp": to_utc_iso(
                self.source_event_timestamp,
                field_name="source_event_timestamp",
            ),
            "subject": dict(self.subject),
            "summary": self.summary,
            "explanation": tuple(self.explanation),
            "evidence": _payload_value(self.evidence),
            "limitations": tuple(self.limitations),
            "provider": self.provider,
        }
        optional_text_payload(payload, "model", self.model)
        return payload


def _payload_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _payload_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return tuple(_payload_value(item) for item in value)
    return value


PUBLISHED_EVENTS = (REASONING_GENERATED,)
