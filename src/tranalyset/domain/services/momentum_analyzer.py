from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from tranalyset.domain.analytics.models import Momentum, MomentumDirection
from tranalyset.domain.common.value_objects import Price, Ratio


@dataclass(frozen=True, slots=True)
class MomentumResult:
    value: Ratio
    direction: MomentumDirection


class MomentumAnalyzer:
    @staticmethod
    def analyze(prices: Sequence[Price], lookback: int = 10) -> MomentumResult:
        if len(prices) < lookback:
            msg = f"Not enough prices. Need at least {lookback}, got {len(prices)}."
            raise ValueError(msg)
        window = prices[-lookback:]
        first = window[0].value
        last = window[-1].value
        if first == 0:
            change = Decimal("0")
        else:
            change = (last - first) / first
        direction = MomentumDirection.ACCELERATING if change > 0 else MomentumDirection.DECELERATING if change < 0 else MomentumDirection.NEUTRAL
        return MomentumResult(value=Ratio(abs(change)), direction=direction)

    @staticmethod
    def to_domain_model(result: MomentumResult) -> Momentum:
        return Momentum(value=result.value, direction=result.direction)
