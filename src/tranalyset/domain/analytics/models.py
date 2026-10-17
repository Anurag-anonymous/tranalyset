from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from tranalyset.domain.common import AssetClass
from tranalyset.domain.common.value_objects import Money, Price, Ratio, Symbol, Volume, quantize_money
from tranalyset.domain.market import Instrument


def _now() -> datetime:
    return datetime.now(UTC)


def _require_aware(timestamp: datetime, field_name: str) -> None:
    if timestamp.tzinfo is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)


def _decimal(value: Decimal | int | float | str) -> Decimal:
    return quantize_money(value if isinstance(value, Decimal) else Decimal(str(value)))


def _freeze_mapping(mapping: Mapping[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(dict(mapping))


class AnalyticsDomainError(ValueError):
    pass


class TrendDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"
    RANGE = "range"


class MomentumDirection(StrEnum):
    ACCELERATING = "accelerating"
    DECELERATING = "decelerating"
    NEUTRAL = "neutral"


class VolatilityLevel(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


@dataclass(frozen=True, slots=True)
class MarketMetric:
    instrument: Instrument
    timestamp: datetime
    value: Decimal | int | float | str
    name: str = "market_metric"

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "MarketMetric timestamp")
        if not self.name.strip():
            msg = "MarketMetric name must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", _decimal(self.value))


@dataclass(frozen=True, slots=True)
class IndicatorValue:
    name: str
    value: Decimal | int | float | str
    timestamp: datetime
    instrument: Instrument | None = None
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "IndicatorValue name must not be empty."
            raise ValueError(msg)
        _require_aware(self.timestamp, "IndicatorValue timestamp")
        object.__setattr__(self, "value", _decimal(self.value))
        if self.timeframe is not None and not self.timeframe.strip():
            msg = "IndicatorValue timeframe must not be empty if provided."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class IndicatorSeries:
    name: str
    values: tuple[IndicatorValue, ...] = ()
    instrument: Instrument | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "IndicatorSeries name must not be empty."
            raise ValueError(msg)
        ordered = tuple(self.values)
        if not ordered:
            msg = "IndicatorSeries values must not be empty."
            raise ValueError(msg)
        last_timestamp = ordered[0].timestamp
        for value in ordered:
            if value.name != self.name:
                msg = "IndicatorSeries values must use the same indicator name."
                raise ValueError(msg)
            if self.instrument is not None and value.instrument is not None and value.instrument != self.instrument:
                msg = "IndicatorSeries values must use the same instrument."
                raise ValueError(msg)
            if value.timestamp < last_timestamp:
                msg = "IndicatorSeries timestamps must be in ascending order."
                raise ValueError(msg)
            last_timestamp = value.timestamp
        object.__setattr__(self, "values", ordered)

    @property
    def latest(self) -> IndicatorValue:
        return self.values[-1]

    def append(self, value: IndicatorValue) -> IndicatorSeries:
        if value.name != self.name:
            msg = "IndicatorSeries value name mismatch."
            raise ValueError(msg)
        if self.values and value.timestamp <= self.values[-1].timestamp:
            msg = "IndicatorSeries timestamps must be strictly increasing."
            raise ValueError(msg)
        return replace(self, values=self.values + (value,))


@dataclass(frozen=True, slots=True)
class PatternConfidence:
    value: Ratio
    confidence: Ratio = Ratio(1)
    explanation: str | None = None

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.value.value <= Decimal("1"):
            msg = "Pattern confidence must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.confidence.value <= Decimal("1"):
            msg = "Pattern confidence certainty must be between 0 and 1."
            raise ValueError(msg)
        if self.explanation is not None and not self.explanation.strip():
            msg = "Pattern confidence explanation must be non-empty if provided."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Pattern:
    pattern_id: str
    name: str
    detected_at: datetime
    confidence: PatternConfidence
    instrument: Instrument | None = None
    direction: TrendDirection | None = None
    evidence: Mapping[str, Decimal | int | float | str | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pattern_id.strip():
            msg = "Pattern pattern_id must not be empty."
            raise ValueError(msg)
        if not self.name.strip():
            msg = "Pattern name must not be empty."
            raise ValueError(msg)
        _require_aware(self.detected_at, "Pattern detected_at")
        object.__setattr__(self, "evidence", _freeze_mapping(self.evidence))

    def __hash__(self) -> int:
        return hash(
            (
                self.pattern_id,
                self.name,
                self.detected_at,
                self.confidence,
                self.instrument,
                self.direction,
                tuple(sorted(self.evidence.items())),
            )
        )


@dataclass(frozen=True, slots=True)
class Trend:
    direction: TrendDirection
    strength: Ratio
    confidence: Ratio = Ratio(1)
    slope: Decimal | int | float | str = Decimal("0")
    description: str | None = None

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.strength.value <= Decimal("1"):
            msg = "Trend strength must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.confidence.value <= Decimal("1"):
            msg = "Trend confidence must be between 0 and 1."
            raise ValueError(msg)
        object.__setattr__(self, "slope", _decimal(self.slope))

    def with_strength(self, strength: Ratio) -> Trend:
        return replace(self, strength=strength)


@dataclass(frozen=True, slots=True)
class Momentum:
    value: Ratio
    direction: MomentumDirection = MomentumDirection.NEUTRAL
    confidence: Ratio = Ratio(1)
    description: str | None = None

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.value.value <= Decimal("1"):
            msg = "Momentum value must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.confidence.value <= Decimal("1"):
            msg = "Momentum confidence must be between 0 and 1."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Volatility:
    value: Ratio
    level: VolatilityLevel = VolatilityLevel.NORMAL
    lookback: int = 1
    annualized: bool = True

    def __post_init__(self) -> None:
        if self.value.value < 0:
            msg = "Volatility value must not be negative."
            raise ValueError(msg)
        if self.lookback <= 0:
            msg = "Volatility lookback must be positive."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SupportResistance:
    support: Price
    resistance: Price
    confidence: Ratio = Ratio(1)

    def __post_init__(self) -> None:
        if self.support.value < 0 or self.resistance.value < 0:
            msg = "Support and resistance must not be negative."
            raise ValueError(msg)
        if self.support.value > self.resistance.value:
            msg = "Support must be less than or equal to resistance."
            raise ValueError(msg)
        if not Decimal("0") <= self.confidence.value <= Decimal("1"):
            msg = "Support/resistance confidence must be between 0 and 1."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MarketStructure:
    trend: Trend | None = None
    momentum: Momentum | None = None
    volatility: Volatility | None = None
    support_resistance: SupportResistance | None = None
    regime: str | None = None
    timestamp: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "MarketStructure timestamp")
        if self.regime is not None and not self.regime.strip():
            msg = "MarketStructure regime must be non-empty if provided."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    symbol: Symbol
    timeframe: str
    price_levels: tuple[tuple[Price, Volume], ...]
    point_of_control: Price
    value_area_high: Price
    value_area_low: Price
    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.timeframe.strip():
            msg = "VolumeProfile timeframe must not be empty."
            raise ValueError(msg)
        if not self.price_levels:
            msg = "VolumeProfile price_levels must not be empty."
            raise ValueError(msg)
        _require_aware(self.timestamp, "VolumeProfile timestamp")
        ordered = tuple((Price(price.value), Volume(volume.value)) for price, volume in self.price_levels)
        object.__setattr__(self, "price_levels", ordered)
        if self.value_area_low.value > self.value_area_high.value:
            msg = "Value area low must be less than or equal to value area high."
            raise ValueError(msg)

    def with_levels(self, price_levels: Sequence[tuple[Price, Volume]]) -> VolumeProfile:
        return replace(self, price_levels=tuple(price_levels))


@dataclass(frozen=True, slots=True)
class TradeStatistics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Ratio
    average_profit: Money
    average_loss: Money
    profit_factor: Ratio
    expectancy: Money
    max_drawdown: Money
    sharpe_ratio: Decimal | int | float | str
    sortino_ratio: Decimal | int | float | str
    timestamp: datetime

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "TradeStatistics timestamp")
        if self.total_trades < 0 or self.winning_trades < 0 or self.losing_trades < 0:
            msg = "Trade statistics counts must not be negative."
            raise ValueError(msg)
        if self.total_trades != self.winning_trades + self.losing_trades:
            msg = "Winning trades plus losing trades must equal total trades."
            raise ValueError(msg)
        if not Decimal("0") <= self.win_rate.value <= Decimal("1"):
            msg = "Win rate must be between 0 and 1."
            raise ValueError(msg)
        if self.average_profit.amount < 0 or self.average_loss.amount < 0:
            msg = "Average profit and loss must not be negative."
            raise ValueError(msg)
        if self.profit_factor.value < 0:
            msg = "Profit factor must not be negative."
            raise ValueError(msg)
        if self.max_drawdown.amount < 0:
            msg = "Max drawdown must not be negative."
            raise ValueError(msg)
        object.__setattr__(self, "sharpe_ratio", _decimal(self.sharpe_ratio))
        object.__setattr__(self, "sortino_ratio", _decimal(self.sortino_ratio))


@dataclass(frozen=True, slots=True)
class StrategyStatistics:
    strategy_id: str
    total_return: Ratio
    annualized_return: Ratio
    max_drawdown: Ratio
    sharpe_ratio: Decimal | int | float | str
    sortino_ratio: Decimal | int | float | str
    win_rate: Ratio
    profit_factor: Ratio
    total_trades: int
    winning_trades: int
    losing_trades: int
    average_trade: Money
    expectancy: Money
    volatility: Ratio
    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            msg = "StrategyStatistics strategy_id must not be empty."
            raise ValueError(msg)
        _require_aware(self.timestamp, "StrategyStatistics timestamp")
        if self.total_trades < 0 or self.winning_trades < 0 or self.losing_trades < 0:
            msg = "Strategy statistics counts must not be negative."
            raise ValueError(msg)
        if self.total_trades != self.winning_trades + self.losing_trades:
            msg = "Winning trades plus losing trades must equal total trades."
            raise ValueError(msg)
        for ratio, label in (
            (self.total_return, "total return"),
            (self.annualized_return, "annualized return"),
            (self.max_drawdown, "max drawdown"),
            (self.win_rate, "win rate"),
            (self.profit_factor, "profit factor"),
            (self.volatility, "volatility"),
        ):
            if label != "profit factor" and ratio.value < -1:
                msg = f"Strategy statistics {label} cannot be less than -100%."
                raise ValueError(msg)
        if not Decimal("0") <= self.max_drawdown.value <= Decimal("1"):
            msg = "Strategy max drawdown must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.win_rate.value <= Decimal("1"):
            msg = "Strategy win rate must be between 0 and 1."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    strategy_id: str
    start_date: datetime
    end_date: datetime
    initial_capital: Money
    final_capital: Money
    total_return: Ratio
    annualized_return: Ratio
    max_drawdown: Ratio
    sharpe_ratio: Decimal | int | float | str
    sortino_ratio: Decimal | int | float | str
    win_rate: Ratio
    profit_factor: Ratio
    total_trades: int
    winning_trades: int
    losing_trades: int
    expectancy: Money
    calibration_params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            msg = "BacktestMetrics strategy_id must not be empty."
            raise ValueError(msg)
        _require_aware(self.start_date, "BacktestMetrics start_date")
        _require_aware(self.end_date, "BacktestMetrics end_date")
        if self.start_date > self.end_date:
            msg = "BacktestMetrics start_date must be before or equal to end_date."
            raise ValueError(msg)
        if self.initial_capital.amount <= 0:
            msg = "Initial capital must be positive."
            raise ValueError(msg)
        if self.final_capital.amount < 0:
            msg = "Final capital must not be negative."
            raise ValueError(msg)
        if not Decimal("0") <= self.max_drawdown.value <= Decimal("1"):
            msg = "Backtest max drawdown must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.win_rate.value <= Decimal("1"):
            msg = "Backtest win rate must be between 0 and 1."
            raise ValueError(msg)
        object.__setattr__(self, "sharpe_ratio", _decimal(self.sharpe_ratio))
        object.__setattr__(self, "sortino_ratio", _decimal(self.sortino_ratio))
        object.__setattr__(self, "calibration_params", _freeze_mapping(self.calibration_params))

    def __hash__(self) -> int:
        return hash(
            (
                self.strategy_id,
                self.start_date,
                self.end_date,
                self.initial_capital,
                self.final_capital,
                self.total_return,
                self.annualized_return,
                self.max_drawdown,
                self.sharpe_ratio,
                self.sortino_ratio,
                self.win_rate,
                self.profit_factor,
                self.total_trades,
                self.winning_trades,
                self.losing_trades,
                self.expectancy,
                tuple(sorted(self.calibration_params.items())),
            )
        )


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    period_start: datetime
    period_end: datetime
    starting_equity: Money
    ending_equity: Money
    absolute_return: Money
    relative_return: Ratio
    annualized_return: Ratio
    max_drawdown: Money
    max_drawdown_percent: Ratio
    sharpe_ratio: Decimal | int | float | str
    sortino_ratio: Decimal | int | float | str
    profit_factor: Ratio
    win_rate: Ratio
    total_trades: int
    winning_trades: int
    losing_trades: int
    expectancy: Money
    volatility: Ratio
    timestamp: datetime

    def __post_init__(self) -> None:
        _require_aware(self.period_start, "PerformanceMetrics period_start")
        _require_aware(self.period_end, "PerformanceMetrics period_end")
        _require_aware(self.timestamp, "PerformanceMetrics timestamp")
        if self.period_start > self.period_end:
            msg = "PerformanceMetrics period_start must be before or equal to period_end."
            raise ValueError(msg)
        if self.starting_equity.amount <= 0:
            msg = "Starting equity must be positive."
            raise ValueError(msg)
        if self.ending_equity.amount < 0:
            msg = "Ending equity must not be negative."
            raise ValueError(msg)
        if not Decimal("0") <= self.max_drawdown_percent.value <= Decimal("1"):
            msg = "PerformanceMetrics max_drawdown_percent must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.win_rate.value <= Decimal("1"):
            msg = "PerformanceMetrics win_rate must be between 0 and 1."
            raise ValueError(msg)
        object.__setattr__(self, "sharpe_ratio", _decimal(self.sharpe_ratio))
        object.__setattr__(self, "sortino_ratio", _decimal(self.sortino_ratio))


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    instrument: Instrument
    timestamp: datetime
    timeframe: str
    indicators: Mapping[str, Decimal | int | float | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "IndicatorSnapshot timestamp")
        if not self.timeframe.strip():
            msg = "IndicatorSnapshot timeframe must not be empty."
            raise ValueError(msg)
        if not self.indicators:
            msg = "IndicatorSnapshot indicators must not be empty."
            raise ValueError(msg)
        object.__setattr__(
            self,
            "indicators",
            _freeze_mapping({key: _decimal(value) for key, value in self.indicators.items()}),
        )

    def __hash__(self) -> int:
        return hash((self.instrument, self.timestamp, self.timeframe, tuple(sorted(self.indicators.items()))))


@dataclass(frozen=True, slots=True)
class PerformanceMetric:
    name: str
    value: Decimal | int | float | str
    confidence: Ratio | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "PerformanceMetric name must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", _decimal(self.value))


__all__ = [
    "AnalyticsDomainError",
    "TrendDirection",
    "MomentumDirection",
    "VolatilityLevel",
    "MarketMetric",
    "IndicatorValue",
    "IndicatorSeries",
    "PatternConfidence",
    "Pattern",
    "Trend",
    "Momentum",
    "Volatility",
    "SupportResistance",
    "MarketStructure",
    "VolumeProfile",
    "TradeStatistics",
    "StrategyStatistics",
    "BacktestMetrics",
    "PerformanceMetrics",
    "IndicatorSnapshot",
    "PerformanceMetric",
]
