from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from tranalyset.domain.analytics.models import MarketStructure, Momentum, MomentumDirection, SupportResistance, Trend, TrendDirection, Volatility
from tranalyset.domain.common.value_objects import Price, Ratio


@dataclass(frozen=True, slots=True)
class MarketStructureResult:
    structure_type: str
    confidence: Ratio
    supporting_factors: dict[str, Decimal]


class MarketStructureAnalyzer:
    @staticmethod
    def analyze(prices: Sequence[Price], lookback: int = 50) -> MarketStructureResult:
        if len(prices) < lookback:
            msg = f"Not enough prices. Need at least {lookback}, got {len(prices)}."
            raise ValueError(msg)

        recent = [price.value for price in prices[-lookback:]]
        first = recent[0]
        last = recent[-1]
        range_low = min(recent)
        range_high = max(recent)
        range_width = range_high - range_low
        change = last - first
        change_ratio = Decimal("0") if first == 0 else change / first

        if abs(change_ratio) < Decimal("0.01") or range_width == 0:
            structure_type = "ranging"
            confidence = Ratio(1 - min(abs(change_ratio) * 10, Decimal("1")))
        elif change_ratio > 0:
            structure_type = "trending_up"
            confidence = Ratio(min(change_ratio * 5, Decimal("1")))
        else:
            structure_type = "trending_down"
            confidence = Ratio(min(abs(change_ratio) * 5, Decimal("1")))

        if structure_type == "trending_up" and last >= range_high * Decimal("0.98"):
            structure_type = "breakout"
        elif structure_type == "trending_down" and last <= range_low * Decimal("1.02"):
            structure_type = "breakdown"

        return MarketStructureResult(
            structure_type=structure_type,
            confidence=confidence,
            supporting_factors={
                "change_ratio": change_ratio,
                "range_width": range_width,
            },
        )

    @staticmethod
    def to_domain_model(
        result: MarketStructureResult,
        *,
        trend: Trend | None = None,
        momentum: Momentum | None = None,
        volatility: Volatility | None = None,
        support_resistance: SupportResistance | None = None,
    ) -> MarketStructure:
        if trend is None:
            trend = Trend(direction=TrendDirection.SIDEWAYS, strength=Ratio(0))
        if momentum is None:
            momentum = Momentum(value=Ratio(0), direction=MomentumDirection.NEUTRAL)
        if volatility is None:
            volatility = Volatility(value=Ratio(0))
        return MarketStructure(
            trend=trend,
            momentum=momentum,
            volatility=volatility,
            support_resistance=support_resistance,
            regime=result.structure_type,
        )
