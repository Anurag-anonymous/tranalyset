from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.payloads import require_text, to_utc_iso

CANDIDATE_TYPES = (
    "TradeSignalCandidate",
    "WatchCandidate",
    "EntryCandidate",
    "ExitCandidate",
    "PartialExitCandidate",
    "TrailCandidate",
)


@dataclass(frozen=True, slots=True)
class SignalCandidatePayload:
    candidate_type: str
    signal_id: str
    symbol: str
    timeframe: str
    generated_at: datetime
    confidence: float
    reasoning: Sequence[str]
    supporting_evidence: Mapping[str, float | str | bool]
    reward_risk_estimate: float
    source_event_type: str

    def to_payload(self) -> dict[str, Any]:
        require_text(self.candidate_type, "candidate_type")
        require_text(self.signal_id, "signal_id")
        require_text(self.symbol, "symbol")
        require_text(self.timeframe, "timeframe")
        require_text(self.source_event_type, "source_event_type")
        if self.candidate_type not in CANDIDATE_TYPES:
            msg = f"Unsupported candidate type: {self.candidate_type}"
            raise ValueError(msg)
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        if self.reward_risk_estimate <= 0:
            msg = "reward_risk_estimate must be greater than zero."
            raise ValueError(msg)
        if not self.reasoning:
            msg = "reasoning must not be empty."
            raise ValueError(msg)
        if not self.supporting_evidence:
            msg = "supporting_evidence must not be empty."
            raise ValueError(msg)
        return {
            "candidate_type": self.candidate_type,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "generated_at": to_utc_iso(self.generated_at, field_name="generated_at"),
            "confidence": self.confidence,
            "reasoning": tuple(self.reasoning),
            "supporting_evidence": dict(self.supporting_evidence),
            "reward_risk_estimate": self.reward_risk_estimate,
            "source_event_type": self.source_event_type,
        }


@dataclass(frozen=True, slots=True)
class TradeSignalCandidate(SignalCandidatePayload):
    candidate_type: str = field(init=False, default="TradeSignalCandidate")


@dataclass(frozen=True, slots=True)
class WatchCandidate(SignalCandidatePayload):
    candidate_type: str = field(init=False, default="WatchCandidate")


@dataclass(frozen=True, slots=True)
class EntryCandidate(SignalCandidatePayload):
    candidate_type: str = field(init=False, default="EntryCandidate")


@dataclass(frozen=True, slots=True)
class ExitCandidate(SignalCandidatePayload):
    candidate_type: str = field(init=False, default="ExitCandidate")


@dataclass(frozen=True, slots=True)
class PartialExitCandidate(SignalCandidatePayload):
    candidate_type: str = field(init=False, default="PartialExitCandidate")


@dataclass(frozen=True, slots=True)
class TrailCandidate(SignalCandidatePayload):
    candidate_type: str = field(init=False, default="TrailCandidate")
