from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from tranalyset.plugins.signal_engine.contracts import SignalCandidatePayload


@dataclass(slots=True)
class SignalEvidence:
    symbol: str = "GLOBAL"
    timeframe: str = "event"
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    trade_id: str | None = None
    health_score: float | None = None
    health_confidence_lower: float | None = None
    health_confidence_upper: float | None = None
    regime: str | None = None
    pattern: str | None = None
    pattern_confidence: float | None = None
    trend_score: float | None = None
    momentum_score: float | None = None
    volume_ratio: float | None = None
    news_sentiment: str | None = None
    news_confidence: float | None = None
    risk_approved: bool = False
    reward_risk_estimate: float | None = None
    source_events: set[str] = field(default_factory=set)


class SignalCandidateGenerator:
    def generate(
        self,
        evidence: SignalEvidence,
        *,
        source_event_type: str,
    ) -> tuple[SignalCandidatePayload, ...]:
        confidence = self._confidence(evidence)
        reward_risk_estimate = self._reward_risk_estimate(evidence)
        supporting_evidence = self._supporting_evidence(evidence)
        candidates: list[SignalCandidatePayload] = []

        if evidence.health_score is None:
            return ()

        if evidence.risk_approved and evidence.health_score >= 65:
            candidates.append(
                self._candidate(
                    "TradeSignalCandidate",
                    evidence,
                    confidence,
                    reward_risk_estimate,
                    supporting_evidence,
                    source_event_type,
                    (
                        "Trade health and risk approval evidence support a candidate opportunity.",
                        "Candidate is informational and does not open or modify a trade.",
                    ),
                )
            )

        if evidence.health_score < 40 or self._negative_context(evidence):
            candidates.append(
                self._candidate(
                    "ExitCandidate",
                    evidence,
                    min(confidence, 0.78),
                    reward_risk_estimate,
                    supporting_evidence,
                    source_event_type,
                    (
                        "Trade health or context weakened enough to produce an exit candidate.",
                        "Candidate is informational and does not close a trade.",
                    ),
                )
            )
        elif evidence.health_score < 55:
            candidates.append(
                self._candidate(
                    "PartialExitCandidate",
                    evidence,
                    min(confidence, 0.74),
                    reward_risk_estimate,
                    supporting_evidence,
                    source_event_type,
                    (
                        "Trade health is mixed and supports a partial-exit candidate.",
                        "Candidate is informational and does not modify a trade.",
                    ),
                )
            )
        elif evidence.health_score >= 75 and evidence.risk_approved:
            candidates.append(
                self._candidate(
                    "EntryCandidate",
                    evidence,
                    confidence,
                    reward_risk_estimate,
                    supporting_evidence,
                    source_event_type,
                    (
                        "Trade health, market context, and risk approval support an entry candidate.",
                        "Candidate is informational and does not open a trade.",
                    ),
                )
            )

        if evidence.health_score >= 65 and self._momentum_positive(evidence):
            candidates.append(
                self._candidate(
                    "TrailCandidate",
                    evidence,
                    min(confidence, 0.82),
                    reward_risk_estimate,
                    supporting_evidence,
                    source_event_type,
                    (
                        "Momentum and health evidence support a trailing-management candidate.",
                        "Candidate is informational and does not update stops.",
                    ),
                )
            )

        if not candidates and evidence.health_score >= 45:
            candidates.append(
                self._candidate(
                    "WatchCandidate",
                    evidence,
                    max(0.35, min(confidence, 0.68)),
                    reward_risk_estimate,
                    supporting_evidence,
                    source_event_type,
                    (
                        "Evidence is incomplete or mixed, so the opportunity remains on watch.",
                        "Candidate is informational and does not open or modify a trade.",
                    ),
                )
            )

        return tuple(candidates)

    def _candidate(
        self,
        candidate_type: str,
        evidence: SignalEvidence,
        confidence: float,
        reward_risk_estimate: float,
        supporting_evidence: dict[str, float | str | bool],
        source_event_type: str,
        reasoning: tuple[str, ...],
    ) -> SignalCandidatePayload:
        signal_key = evidence.trade_id or f"{evidence.symbol}:{evidence.timeframe}"
        return SignalCandidatePayload(
            candidate_type=candidate_type,
            signal_id=f"{signal_key}:{candidate_type}",
            symbol=evidence.symbol,
            timeframe=evidence.timeframe,
            generated_at=evidence.observed_at,
            confidence=round(confidence, 4),
            reasoning=reasoning,
            supporting_evidence=supporting_evidence,
            reward_risk_estimate=round(reward_risk_estimate, 4),
            source_event_type=source_event_type,
        )

    def _confidence(self, evidence: SignalEvidence) -> float:
        confidence_parts = []
        if evidence.health_score is not None:
            confidence_parts.append(evidence.health_score / 100.0)
        if evidence.health_confidence_lower is not None and evidence.health_confidence_upper is not None:
            interval_width = evidence.health_confidence_upper - evidence.health_confidence_lower
            confidence_parts.append(max(0.2, 1.0 - interval_width / 100.0))
        if evidence.pattern_confidence is not None:
            confidence_parts.append(evidence.pattern_confidence)
        if evidence.news_confidence is not None:
            confidence_parts.append(evidence.news_confidence)
        if evidence.risk_approved:
            confidence_parts.append(0.85)
        if not confidence_parts:
            return 0.35
        return max(0.0, min(0.95, sum(confidence_parts) / len(confidence_parts)))

    def _reward_risk_estimate(self, evidence: SignalEvidence) -> float:
        if evidence.reward_risk_estimate is not None and evidence.reward_risk_estimate > 0:
            return evidence.reward_risk_estimate
        health_component = (evidence.health_score or 50.0) / 50.0
        momentum_component = 1.0 + max(-0.4, min(0.6, evidence.momentum_score or 0.0))
        risk_component = 1.15 if evidence.risk_approved else 0.85
        return max(0.1, health_component * momentum_component * risk_component)

    def _supporting_evidence(self, evidence: SignalEvidence) -> dict[str, float | str | bool]:
        values: dict[str, float | str | bool] = {
            "risk_approved": evidence.risk_approved,
            "source_event_count": len(evidence.source_events),
        }
        for key in (
            "health_score",
            "health_confidence_lower",
            "health_confidence_upper",
            "regime",
            "pattern",
            "pattern_confidence",
            "trend_score",
            "momentum_score",
            "volume_ratio",
            "news_sentiment",
            "news_confidence",
        ):
            value = getattr(evidence, key)
            if isinstance(value, str | int | float | bool):
                values[key] = value
        return values

    def _negative_context(self, evidence: SignalEvidence) -> bool:
        regime = (evidence.regime or "").lower()
        sentiment = (evidence.news_sentiment or "").lower()
        return (
            "bear" in regime
            or "breakdown" in regime
            or "false breakout" in regime
            or sentiment in {"negative", "bearish", "risk_off"}
        )

    def _momentum_positive(self, evidence: SignalEvidence) -> bool:
        regime = (evidence.regime or "").lower()
        return (evidence.momentum_score or 0.0) >= 0.55 or "strong momentum" in regime
