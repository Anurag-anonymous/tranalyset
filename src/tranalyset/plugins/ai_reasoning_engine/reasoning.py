from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from tranalyset.contracts.events import EventEnvelope, ImmutableValue


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    reference_id: str
    source: str
    content: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "source": self.source,
            "content": dict(self.content),
        }


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    category: str
    source_event: EventEnvelope
    subject: Mapping[str, str]
    evidence: Mapping[str, Any]
    retrieved_context: Sequence[EvidenceReference] = ()


@dataclass(frozen=True, slots=True)
class ReasoningResponse:
    summary: str
    explanation: tuple[str, ...]
    limitations: tuple[str, ...]
    provider: str
    model: str | None = None


@runtime_checkable
class ReasoningProvider(Protocol):
    async def generate(self, request: ReasoningRequest) -> ReasoningResponse:
        """Generate an explanation from the supplied evidence only."""


@runtime_checkable
class EvidenceRetriever(Protocol):
    async def retrieve(self, event: EventEnvelope) -> Sequence[EvidenceReference]:
        """Return additional evidence references for future RAG-enabled reasoning."""


class DeterministicReasoningProvider:
    async def generate(self, request: ReasoningRequest) -> ReasoningResponse:
        source_event_type = str(request.source_event.event_type)
        subject_text = _subject_text(request.subject)
        observed = _observed_fields(request.evidence)
        explanation = (
            f"{source_event_type} was received for {subject_text}.",
            f"Available evidence fields: {observed}.",
            _category_statement(request.category, request.evidence),
        )
        limitations = _limitations(request)
        return ReasoningResponse(
            summary=f"Explained {source_event_type} using received evidence.",
            explanation=explanation,
            limitations=limitations,
            provider="deterministic",
        )


def evidence_from_event(event: EventEnvelope) -> dict[str, Any]:
    return {
        "event": {
            "timestamp": event.timestamp.isoformat(),
            "source": event.source,
            "event_type": str(event.event_type),
            "payload": _unfreeze(event.payload),
            "metadata": _unfreeze(event.metadata),
        }
    }


def _category_statement(category: str, evidence: Mapping[str, Any]) -> str:
    payload = _payload(evidence)
    if category == "trade_health":
        return _fields_statement(payload, ("health_score", "previous_health_score", "confidence_lower", "confidence_upper", "explanations"))
    if category == "risk":
        return _fields_statement(payload, ("reason", "evidence", "quantity", "risk_amount", "stop_loss_price"))
    if category in {"entry_candidate", "exit_candidate"}:
        return _fields_statement(payload, ("candidate_type", "confidence", "reasoning", "supporting_evidence", "reward_risk_estimate"))
    if category == "trailing_stop":
        return _fields_statement(payload, ("trailing_stop_price", "previous_trailing_stop_price", "reason"))
    if category == "market_regime":
        return _fields_statement(payload, ("regime", "state", "previous_regime", "previous_state", "confidence", "evidence"))
    if category == "pattern_detection":
        return _fields_statement(payload, ("pattern", "confidence", "evidence", "reason"))
    return "The explanation is limited to the received event payload."


def _fields_statement(payload: Mapping[str, Any], field_names: Sequence[str]) -> str:
    present = tuple(field for field in field_names if field in payload)
    if not present:
        return "No category-specific fields were present beyond the event envelope."
    values = ", ".join(f"{field}={payload[field]!r}" for field in present)
    return f"Category evidence: {values}."


def _limitations(request: ReasoningRequest) -> tuple[str, ...]:
    limitations = ["No data outside the received event evidence was used."]
    if request.retrieved_context:
        limitations.append("Retrieved context was attached as evidence and not treated as market truth.")
    else:
        limitations.append("No retrieval context was supplied.")
    return tuple(limitations)


def _observed_fields(evidence: Mapping[str, Any]) -> str:
    payload = _payload(evidence)
    if not payload:
        return "none"
    return ", ".join(sorted(str(key) for key in payload))


def _payload(evidence: Mapping[str, Any]) -> Mapping[str, Any]:
    event = evidence.get("event")
    if isinstance(event, Mapping):
        payload = event.get("payload")
        if isinstance(payload, Mapping):
            return payload
    return {}


def _subject_text(subject: Mapping[str, str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(subject.items()))


def _unfreeze(value: ImmutableValue | object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _unfreeze(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_unfreeze(item) for item in value)
    return value
