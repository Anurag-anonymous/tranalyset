from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import StatisticsError, stdev
from typing import Sequence

from tranalyset.domain.analytics.models import Volatility as VolatilityModel, VolatilityLevel
from tranalyset.domain.common.value_objects import Price, Ratio


@dataclass(frozen=True, slots=True)
class VolatilityResult:
    value: Ratio
    annualized: bool
    lookback: int


class VolatilityCalculator:
    @staticmethod
    def calculate_historical_volatility(
        prices: Sequence[Price],
        trading_periods_per_year: int = 252,
    ) -> VolatilityResult:
        if len(prices) < 2:
            msg = "At least two prices are required to calculate volatility."
            raise ValueError(msg)

        log_returns: list[float] = []
        for index in range(1, len(prices)):
            previous = prices[index - 1].value
            current = prices[index].value
            if previous <= 0 or current <= 0:
                msg = "Prices must be positive for log return calculation."
                raise ValueError(msg)
            log_returns.append(math.log(float(current / previous)))

        if len(log_returns) < 2:
            return VolatilityResult(value=Ratio(0), annualized=True, lookback=len(prices) - 1)

        try:
            deviation = stdev(log_returns)
        except StatisticsError:
            deviation = 0.0

        annualized = deviation * math.sqrt(trading_periods_per_year)
        return VolatilityResult(value=Ratio(annualized), annualized=True, lookback=len(prices) - 1)

    @staticmethod
    def to_domain_model(
        prices: Sequence[Price],
        *,
        lookback: int,
        level: VolatilityLevel = VolatilityLevel.NORMAL,
    ) -> VolatilityModel:
        if lookback <= 0:
            msg = "Lookback must be positive."
            raise ValueError(msg)
        if len(prices) < lookback:
            msg = f"Not enough prices. Need at least {lookback}, got {len(prices)}."
            raise ValueError(msg)
        result = VolatilityCalculator.calculate_historical_volatility(prices[-lookback:])
        return VolatilityModel(value=result.value, level=level, lookback=lookback)

    @staticmethod
    def calculate_atr(
        high_prices: Sequence[Price],
        low_prices: Sequence[Price],
        close_prices: Sequence[Price],
        lookback: int = 14,
    ) -> Price:
        if not (len(high_prices) == len(low_prices) == len(close_prices)):
            msg = "All price lists must have the same length."
            raise ValueError(msg)
        if len(close_prices) < 2:
            msg = "At least two periods are required for ATR."
            raise ValueError(msg)
        if lookback <= 0:
            msg = "Lookback must be positive."
            raise ValueError(msg)

        true_ranges: list[Decimal] = []
        for index in range(1, len(close_prices)):
            high = high_prices[index].value
            low = low_prices[index].value
            previous_close = close_prices[index - 1].value
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

        window = true_ranges[-lookback:] if len(true_ranges) >= lookback else true_ranges
        if not window:
            return Price(0)
        return Price(sum(window) / Decimal(len(window)))
