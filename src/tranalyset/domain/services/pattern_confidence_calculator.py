from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tranalyset.domain.analytics.models import Pattern, PatternConfidence
from tranalyset.domain.common.value_objects import Ratio


@dataclass(frozen=True, slots=True)
class PatternConfidenceResult:
    confidence: Ratio
    factors: dict[str, Decimal]


class PatternConfidenceCalculator:
    @staticmethod
    def calculate(
        pattern: Pattern,
        *,
        volume_confirmation: bool = False,
        momentum_alignment: bool = False,
        timeframe_alignment: bool = False,
        historical_success_rate: float = 0.5,
    ) -> PatternConfidenceResult:
        base_confidence = pattern.confidence.value.value
        adjustments = Decimal("0")
        adjustments += Decimal("0.1") if volume_confirmation else Decimal("-0.05")
        adjustments += Decimal("0.1") if momentum_alignment else Decimal("-0.05")
        adjustments += Decimal("0.1") if timeframe_alignment else Decimal("-0.05")
        adjustments += Decimal(str((historical_success_rate - 0.5) * 0.2))

        adjusted = max(Decimal("0"), min(Decimal("1"), base_confidence + adjustments))
        return PatternConfidenceResult(
            confidence=Ratio(adjusted),
            factors={
                "base_confidence": base_confidence,
                "adjustments": adjustments,
                "adjusted_confidence": adjusted,
            },
        )

    @staticmethod
    def to_domain_model(
        result: PatternConfidenceResult,
        *,
        explanation: str | None = None,
    ) -> PatternConfidence:
        return PatternConfidence(value=result.confidence, confidence=Ratio(1), explanation=explanation)
