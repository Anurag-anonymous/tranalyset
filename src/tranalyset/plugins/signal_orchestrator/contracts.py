from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso
from tranalyset.plugins.signal_engine.contracts import CANDIDATE_TYPES

WATCH_CANDIDATE_GENERATED = EventType("WatchCandidateGenerated")
ACCEPTED_CANDIDATE = EventType("AcceptedCandidate")
REJECTED_CANDIDATE = EventType("RejectedCandidate")

CANDIDATE_DECISIONS = ("accepted", "rejected", "watch")


def _base_payload(
    *,
    signal_id: str,
    strategy_id: str,
    candidate_type: str,
    symbol: str,
    timeframe: str,
    generated_at: datetime,
    confidence: float,
    reasoning: Sequence[str],
    supporting_evidence: Mapping[str, float | str | bool],
    reward_risk_estimate: float,
    source_event_type: str,
    decision: str,
    score: float,
    rank: int,
    vote_count: int,
    strategy_weight: float,
    regime_weight: float,
    decision_at: datetime,
    source_plugin: str,
) -> dict[str, Any]:
    require_text(signal_id, "signal_id")
    require_text(strategy_id, "strategy_id")
    require_text(candidate_type, "candidate_type")
    require_text(symbol, "symbol")
    require_text(timeframe, "timeframe")
    require_text(source_event_type, "source_event_type")
    require_text(decision, "decision")
    require_text(source_plugin, "source_plugin")
    if candidate_type not in CANDIDATE_TYPES:
        msg = f"Unsupported candidate_type: {candidate_type}"
        raise ValueError(msg)
    if decision not in CANDIDATE_DECISIONS:
        msg = f"Unsupported decision: {decision}"
        raise ValueError(msg)
    if not 0.0 <= confidence <= 1.0:
        msg = "confidence must be between 0.0 and 1.0."
        raise ValueError(msg)
    if score < 0.0:
        msg = "score must not be negative."
        raise ValueError(msg)
    if rank < 1:
        msg = "rank must be greater than zero."
        raise ValueError(msg)
    if vote_count < 1:
        msg = "vote_count must be at least one."
        raise ValueError(msg)
    if strategy_weight < 0.0 or regime_weight < 0.0:
        msg = "weights must not be negative."
        raise ValueError(msg)
    if reward_risk_estimate <= 0:
        msg = "reward_risk_estimate must be greater than zero."
        raise ValueError(msg)

    payload: dict[str, Any] = {
        "signal_id": signal_id,
        "strategy_id": strategy_id,
        "candidate_type": candidate_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "generated_at": to_utc_iso(generated_at, field_name="generated_at"),
        "confidence": confidence,
        "reasoning": tuple(reasoning),
        "supporting_evidence": dict(supporting_evidence),
        "reward_risk_estimate": reward_risk_estimate,
        "source_event_type": source_event_type,
        "decision": decision,
        "score": score,
        "rank": rank,
        "vote_count": vote_count,
        "strategy_weight": strategy_weight,
        "regime_weight": regime_weight,
        "decision_at": to_utc_iso(decision_at, field_name="decision_at"),
        "source_plugin": source_plugin,
    }
    return payload


@dataclass(frozen=True, slots=True)
class OrchestratedCandidatePayload:
    signal_id: str
    strategy_id: str
    candidate_type: str
    symbol: str
    timeframe: str
    generated_at: datetime
    confidence: float
    reasoning: Sequence[str]
    supporting_evidence: Mapping[str, float | str | bool]
    reward_risk_estimate: float
    source_event_type: str
    decision: str
    score: float
    rank: int
    vote_count: int
    strategy_weight: float
    regime_weight: float
    decision_at: datetime
    source_plugin: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.decision, "decision")
        if self.decision not in CANDIDATE_DECISIONS:
            msg = f"Unsupported decision: {self.decision}"
            raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            signal_id=self.signal_id,
            strategy_id=self.strategy_id,
            candidate_type=self.candidate_type,
            symbol=self.symbol,
            timeframe=self.timeframe,
            generated_at=self.generated_at,
            confidence=self.confidence,
            reasoning=self.reasoning,
            supporting_evidence=self.supporting_evidence,
            reward_risk_estimate=self.reward_risk_estimate,
            source_event_type=self.source_event_type,
            decision=self.decision,
            score=self.score,
            rank=self.rank,
            vote_count=self.vote_count,
            strategy_weight=self.strategy_weight,
            regime_weight=self.regime_weight,
            decision_at=self.decision_at,
            source_plugin=self.source_plugin,
        )
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


__all__ = [
    "ACCEPTED_CANDIDATE",
    "CANDIDATE_DECISIONS",
    "OrchestratedCandidatePayload",
    "REJECTED_CANDIDATE",
    "WATCH_CANDIDATE_GENERATED",
]