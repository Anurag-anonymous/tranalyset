from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tranalyset.domain.analytics.models import IndicatorSeries, IndicatorValue
from tranalyset.domain.market import Instrument


@dataclass(frozen=True, slots=True)
class IndicatorFactory:
    @staticmethod
    def create_value(
        name: str,
        value: float | int | str,
        timestamp: datetime,
        *,
        instrument: Instrument | None = None,
        timeframe: str | None = None,
    ) -> IndicatorValue:
        return IndicatorValue(name=name, value=value, timestamp=timestamp, instrument=instrument, timeframe=timeframe)

    @staticmethod
    def create_series(
        name: str,
        values: tuple[IndicatorValue, ...],
        *,
        instrument: Instrument | None = None,
    ) -> IndicatorSeries:
        return IndicatorSeries(name=name, values=values, instrument=instrument)
