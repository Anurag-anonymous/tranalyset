from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from tranalyset.domain.analytics.models import Trend, TrendDirection
from tranalyset.domain.common.value_objects import Price, Ratio


@dataclass(frozen=True, slots=True)
class TrendResult:
    direction: TrendDirection
    strength: Ratio
    slope: Decimal
    r_squared: Ratio


class TrendAnalyzer:
    @staticmethod
    def analyze(prices: Sequence[Price], lookback: int = 20) -> TrendResult:
        if len(prices) < lookback:
            msg = f"Not enough prices. Need at least {lookback}, got {len(prices)}."
            raise ValueError(msg)

        closes = [price.value for price in prices[-lookback:]]
        if len(closes) < 2:
            return TrendResult(direction=TrendDirection.SIDEWAYS, strength=Ratio(0), slope=Decimal("0"), r_squared=Ratio(0))

        n = Decimal(len(closes))
        xs = [Decimal(index) for index in range(len(closes))]
        sum_x = sum(xs, Decimal("0"))
        sum_y = sum(closes, Decimal("0"))
        sum_xy = sum(x * y for x, y in zip(xs, closes))
        sum_x2 = sum(x * x for x in xs)

        denominator = n * sum_x2 - sum_x * sum_x
        slope = Decimal("0") if denominator == 0 else (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n

        predicted = [intercept + slope * x for x in xs]
        mean = sum_y / n
        ss_tot = sum((price - mean) ** 2 for price in closes)
        ss_res = sum((closes[index] - predicted[index]) ** 2 for index in range(len(closes)))
        r_squared = Decimal("0") if ss_tot == 0 else max(Decimal("0"), min(Decimal("1"), Decimal("1") - (ss_res / ss_tot)))

        slope_threshold = Decimal("0.0001")
        if abs(slope) < slope_threshold:
            direction = TrendDirection.SIDEWAYS
        elif slope > 0:
            direction = TrendDirection.UP
        else:
            direction = TrendDirection.DOWN

        return TrendResult(
            direction=direction,
            strength=Ratio(r_squared),
            slope=slope,
            r_squared=Ratio(r_squared),
        )

    @staticmethod
    def to_domain_model(result: TrendResult, *, description: str | None = None) -> Trend:
        return Trend(direction=result.direction, strength=result.strength, slope=result.slope, description=description)
