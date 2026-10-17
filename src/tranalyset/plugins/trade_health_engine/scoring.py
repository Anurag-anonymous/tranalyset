from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_FACTORS, normalize_weights


@dataclass(frozen=True, slots=True)
class TradeHealthSnapshot:
    health_score: float
    confidence_lower: float
    confidence_upper: float
    strength: str
    factors: Mapping[str, float]
    weights: Mapping[str, float]
    explanations: tuple[str, ...]
    evidence_count: int


@dataclass(slots=True)
class TradeHealthEvidence:
    factors: dict[str, float] = field(default_factory=lambda: {factor: 50.0 for factor in TRADE_HEALTH_FACTORS})
    factor_evidence: dict[str, int] = field(default_factory=lambda: {factor: 0 for factor in TRADE_HEALTH_FACTORS})
    evidence_count: int = 0

    def update_factor(self, factor: str, score: float) -> None:
        if factor not in TRADE_HEALTH_FACTORS:
            msg = f"Unsupported trade health factor: {factor}"
            raise ValueError(msg)
        self.factors[factor] = _clamp(score)
        self.factor_evidence[factor] += 1
        self.evidence_count += 1


class TradeHealthScorer:
    def __init__(self, *, weights: Mapping[str, object] | None = None) -> None:
        self._weights = normalize_weights(weights)

    @property
    def weights(self) -> Mapping[str, float]:
        return dict(self._weights)

    def score(
        self,
        evidence: TradeHealthEvidence,
        previous: TradeHealthSnapshot | None,
    ) -> TradeHealthSnapshot:
        weighted_total = 0.0
        weight_total = 0.0
        for factor, value in evidence.factors.items():
            weight = self._weights[factor]
            weighted_total += value * weight
            weight_total += weight
        health_score = round(weighted_total / weight_total, 4) if weight_total else 50.0
        interval_width = self._confidence_width(evidence)
        confidence_lower = round(max(0.0, health_score - interval_width), 4)
        confidence_upper = round(min(100.0, health_score + interval_width), 4)
        strength = strength_for_score(health_score)
        explanations = self._explain(evidence, previous, health_score, confidence_lower, confidence_upper)
        return TradeHealthSnapshot(
            health_score=health_score,
            confidence_lower=confidence_lower,
            confidence_upper=confidence_upper,
            strength=strength,
            factors=dict(evidence.factors),
            weights=dict(self._weights),
            explanations=tuple(explanations),
            evidence_count=evidence.evidence_count,
        )

    def _confidence_width(self, evidence: TradeHealthEvidence) -> float:
        covered_factors = sum(1 for count in evidence.factor_evidence.values() if count > 0)
        coverage_ratio = covered_factors / len(TRADE_HEALTH_FACTORS)
        evidence_depth = min(evidence.evidence_count / 20.0, 1.0)
        return max(4.0, 28.0 - coverage_ratio * 14.0 - evidence_depth * 10.0)

    def _explain(
        self,
        evidence: TradeHealthEvidence,
        previous: TradeHealthSnapshot | None,
        health_score: float,
        confidence_lower: float,
        confidence_upper: float,
    ) -> list[str]:
        explanations = [
            f"Trade health score is {health_score:.2f} from weighted evidence factors.",
            f"Confidence interval is {confidence_lower:.2f}-{confidence_upper:.2f} based on evidence coverage.",
        ]
        if previous is None:
            strongest_factor = max(evidence.factors, key=lambda factor: evidence.factors[factor])
            weakest_factor = min(evidence.factors, key=lambda factor: evidence.factors[factor])
            explanations.append(
                f"Strongest factor is {strongest_factor}={evidence.factors[strongest_factor]:.2f}; "
                f"weakest factor is {weakest_factor}={evidence.factors[weakest_factor]:.2f}."
            )
            return explanations

        delta = health_score - previous.health_score
        direction = "increased" if delta >= 0 else "decreased"
        explanations.append(f"Trade health score {direction} by {abs(delta):.2f} points.")
        factor_deltas = {
            factor: evidence.factors[factor] - previous.factors.get(factor, 50.0)
            for factor in evidence.factors
        }
        changed = sorted(factor_deltas.items(), key=lambda item: abs(item[1]), reverse=True)
        if changed and abs(changed[0][1]) > 0:
            explanations.append(
                f"Largest factor change is {changed[0][0]} by {changed[0][1]:.2f} points."
            )
        return explanations


def strength_for_score(score: float) -> str:
    if score >= 70.0:
        return "Strong"
    if score < 40.0:
        return "Weak"
    return "Neutral"


def _clamp(value: float) -> float:
    return min(100.0, max(0.0, float(value)))
