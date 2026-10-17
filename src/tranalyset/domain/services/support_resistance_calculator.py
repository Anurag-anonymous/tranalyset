from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from tranalyset.domain.analytics.models import SupportResistance
from tranalyset.domain.common.value_objects import Price, Ratio


@dataclass(frozen=True, slots=True)
class SupportResistanceResult:
    support: Price
    resistance: Price
    confidence: Ratio


class SupportResistanceCalculator:
    @staticmethod
    def calculate(prices: Sequence[Price]) -> SupportResistanceResult:
        if not prices:
            msg = "At least one price is required."
            raise ValueError(msg)
        support = min(prices, key=lambda price: price.value)
        resistance = max(prices, key=lambda price: price.value)
        confidence = Ratio(1 if support != resistance else 0.5)
        return SupportResistanceResult(support=support, resistance=resistance, confidence=confidence)

    @staticmethod
    def to_domain_model(result: SupportResistanceResult) -> SupportResistance:
        return SupportResistance(support=result.support, resistance=result.resistance, confidence=result.confidence)
