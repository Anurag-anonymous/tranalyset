from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from tranalyset.domain.common import OrderSide
from tranalyset.domain.common.value_objects import Ratio, quantize_money
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


class StrategyDomainError(ValueError):
    pass


class StrategyDecisionType(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    MODIFY = "modify"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class StrategyId:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            msg = "StrategyId must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", self.value.strip())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StrategyParameter:
    name: str
    value: Any
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "StrategyParameter name must not be empty."
            raise ValueError(msg)
        if self.description is not None and not self.description.strip():
            msg = "StrategyParameter description must be non-empty if provided."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class StrategyConfiguration:
    strategy_id: StrategyId
    parameters: tuple[StrategyParameter, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "StrategyConfiguration created_at")
        object.__setattr__(self, "parameters", tuple(self.parameters))
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            msg = "StrategyConfiguration parameter names must be unique."
            raise ValueError(msg)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def with_parameter(self, parameter: StrategyParameter) -> StrategyConfiguration:
        parameters = tuple(item for item in self.parameters if item.name != parameter.name) + (parameter,)
        return replace(self, parameters=parameters)

    def __hash__(self) -> int:
        return hash((self.strategy_id, self.parameters, tuple(sorted(self.metadata.items())), self.created_at))


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_id: StrategyId
    name: str
    description: str | None = None
    author: str | None = None
    tags: tuple[str, ...] = ()
    instruments: tuple[Instrument, ...] = ()
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "StrategyMetadata name must not be empty."
            raise ValueError(msg)
        if self.description is not None and not self.description.strip():
            msg = "StrategyMetadata description must be non-empty if provided."
            raise ValueError(msg)
        if self.author is not None and not self.author.strip():
            msg = "StrategyMetadata author must be non-empty if provided."
            raise ValueError(msg)
        _require_aware(self.created_at, "StrategyMetadata created_at")
        _require_aware(self.updated_at, "StrategyMetadata updated_at")
        if self.created_at > self.updated_at:
            msg = "StrategyMetadata created_at must be before or equal to updated_at."
            raise ValueError(msg)
        object.__setattr__(self, "tags", tuple(tag for tag in self.tags if tag.strip()))
        object.__setattr__(self, "instruments", tuple(self.instruments))


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    strategy_id: StrategyId
    version_number: str
    configuration: StrategyConfiguration
    description: str | None = None
    release_date: datetime = field(default_factory=_now)
    is_active: bool = False

    def __post_init__(self) -> None:
        if not self.version_number.strip():
            msg = "StrategyVersion version_number must not be empty."
            raise ValueError(msg)
        if self.description is not None and not self.description.strip():
            msg = "StrategyVersion description must be non-empty if provided."
            raise ValueError(msg)
        _require_aware(self.release_date, "StrategyVersion release_date")


@dataclass(frozen=True, slots=True)
class StrategySignal:
    signal_id: str
    instrument: Instrument
    side: OrderSide
    strength: Ratio | Decimal | int | float | str
    confidence: Ratio | Decimal | int | float | str
    timestamp: datetime
    strategy_id: StrategyId | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            msg = "StrategySignal signal_id must not be empty."
            raise ValueError(msg)
        _require_aware(self.timestamp, "StrategySignal timestamp")
        strength = self.strength if isinstance(self.strength, Ratio) else Ratio(self.strength)
        confidence = self.confidence if isinstance(self.confidence, Ratio) else Ratio(self.confidence)
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "confidence", confidence)
        if not Decimal("0") <= self.strength.value <= Decimal("1"):
            msg = "StrategySignal strength must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.confidence.value <= Decimal("1"):
            msg = "StrategySignal confidence must be between 0 and 1."
            raise ValueError(msg)
        if self.reason is not None and not self.reason.strip():
            msg = "StrategySignal reason must be non-empty if provided."
            raise ValueError(msg)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def __hash__(self) -> int:
        return hash(
            (
                self.signal_id,
                self.instrument,
                self.side,
                self.strength,
                self.confidence,
                self.timestamp,
                self.strategy_id,
                tuple(sorted(self.metadata.items())),
                self.reason,
            )
        )


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    instrument: Instrument
    side: OrderSide
    strength: float
    confidence: float
    timestamp: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)
    strategy_id: StrategyId | None = None

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            msg = "Signal ID must not be empty."
            raise ValueError(msg)
        _require_aware(self.timestamp, "Signal timestamp")
        if not (0.0 <= self.strength <= 1.0):
            msg = "Strength must be between 0.0 and 1.0."
            raise ValueError(msg)
        if not (0.0 <= self.confidence <= 1.0):
            msg = "Confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def __hash__(self) -> int:
        return hash(
            (
                self.signal_id,
                self.instrument,
                self.side,
                self.strength,
                self.confidence,
                self.timestamp,
                tuple(sorted(self.metadata.items())),
                self.strategy_id,
            )
        )


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    candidate_id: str
    strategy_id: StrategyId
    name: str
    description: str | None
    generation_method: str
    fitness_score: Ratio
    parameters: tuple[StrategyParameter, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            msg = "StrategyCandidate candidate_id must not be empty."
            raise ValueError(msg)
        if not self.name.strip():
            msg = "StrategyCandidate name must not be empty."
            raise ValueError(msg)
        if self.description is not None and not self.description.strip():
            msg = "StrategyCandidate description must be non-empty if provided."
            raise ValueError(msg)
        if not self.generation_method.strip():
            msg = "StrategyCandidate generation_method must not be empty."
            raise ValueError(msg)
        _require_aware(self.generated_at, "StrategyCandidate generated_at")
        if not Decimal("0") <= self.fitness_score.value <= Decimal("1"):
            msg = "StrategyCandidate fitness score must be between 0 and 1."
            raise ValueError(msg)
        object.__setattr__(self, "parameters", tuple(self.parameters))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def __hash__(self) -> int:
        return hash(
            (
                self.candidate_id,
                self.strategy_id,
                self.name,
                self.description,
                self.generation_method,
                self.fitness_score,
                self.parameters,
                tuple(sorted(self.metadata.items())),
                self.generated_at,
            )
        )


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    decision_id: str
    strategy_id: StrategyId
    outcome: StrategyDecisionType
    reason: str
    confidence: Ratio = Ratio(1)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    decided_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            msg = "StrategyDecision decision_id must not be empty."
            raise ValueError(msg)
        if not self.reason.strip():
            msg = "StrategyDecision reason must not be empty."
            raise ValueError(msg)
        _require_aware(self.decided_at, "StrategyDecision decided_at")
        if not Decimal("0") <= self.confidence.value <= Decimal("1"):
            msg = "StrategyDecision confidence must be between 0 and 1."
            raise ValueError(msg)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def __hash__(self) -> int:
        return hash(
            (
                self.decision_id,
                self.strategy_id,
                self.outcome,
                self.reason,
                self.confidence,
                tuple(sorted(self.metadata.items())),
                self.decided_at,
            )
        )


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    evaluation_id: str
    strategy_id: StrategyId
    decision: StrategyDecisionType
    score: Ratio
    feedback: str | None = None
    metrics: Any | None = None
    timestamp: datetime = field(default_factory=_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.evaluation_id.strip():
            msg = "StrategyEvaluation evaluation_id must not be empty."
            raise ValueError(msg)
        _require_aware(self.timestamp, "StrategyEvaluation timestamp")
        if not Decimal("0") <= self.score.value <= Decimal("1"):
            msg = "StrategyEvaluation score must be between 0 and 1."
            raise ValueError(msg)
        if self.feedback is not None and not self.feedback.strip():
            msg = "StrategyEvaluation feedback must be non-empty if provided."
            raise ValueError(msg)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def __hash__(self) -> int:
        return hash(
            (
                self.evaluation_id,
                self.strategy_id,
                self.decision,
                self.score,
                self.feedback,
                self.metrics,
                self.timestamp,
                tuple(sorted(self.metadata.items())),
            )
        )


@dataclass(frozen=True, slots=True)
class StrategyPerformance:
    strategy_id: StrategyId
    period_start: datetime
    period_end: datetime
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
    average_trade: Any
    expectancy: Any
    volatility: Ratio
    timestamp: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        _require_aware(self.period_start, "StrategyPerformance period_start")
        _require_aware(self.period_end, "StrategyPerformance period_end")
        _require_aware(self.timestamp, "StrategyPerformance timestamp")
        if self.period_start > self.period_end:
            msg = "StrategyPerformance period_start must be before or equal to period_end."
            raise ValueError(msg)
        if self.total_trades < 0 or self.winning_trades < 0 or self.losing_trades < 0:
            msg = "StrategyPerformance trade counts must not be negative."
            raise ValueError(msg)
        if self.total_trades != self.winning_trades + self.losing_trades:
            msg = "StrategyPerformance winning trades plus losing trades must equal total trades."
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
                msg = f"StrategyPerformance {label} cannot be less than -100%."
                raise ValueError(msg)
        if not Decimal("0") <= self.max_drawdown.value <= Decimal("1"):
            msg = "StrategyPerformance max_drawdown must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") <= self.win_rate.value <= Decimal("1"):
            msg = "StrategyPerformance win_rate must be between 0 and 1."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Strategy:
    strategy_id: StrategyId
    metadata: StrategyMetadata
    configuration: StrategyConfiguration
    current_version: StrategyVersion
    versions: tuple[StrategyVersion, ...] = ()
    active_configuration: StrategyConfiguration | None = None

    def __post_init__(self) -> None:
        if self.metadata.strategy_id != self.strategy_id:
            msg = "Strategy metadata strategy_id must match strategy_id."
            raise ValueError(msg)
        if self.configuration.strategy_id != self.strategy_id:
            msg = "Strategy configuration strategy_id must match strategy_id."
            raise ValueError(msg)
        if self.current_version.strategy_id != self.strategy_id:
            msg = "Strategy version strategy_id must match strategy_id."
            raise ValueError(msg)
        object.__setattr__(self, "versions", tuple(self.versions))
        if self.versions and self.current_version not in self.versions:
            msg = "Current version must be present in versions."
            raise ValueError(msg)
        if self.active_configuration is not None and self.active_configuration.strategy_id != self.strategy_id:
            msg = "Active configuration strategy_id must match strategy_id."
            raise ValueError(msg)

    def with_version(self, version: StrategyVersion) -> Strategy:
        versions = tuple(dict.fromkeys((*self.versions, version)))
        return replace(self, current_version=version, versions=versions)


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_id: StrategyId
    name: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "StrategyDefinition name must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "parameters", _freeze_mapping(self.parameters))

    def __hash__(self) -> int:
        return hash((self.strategy_id, self.name, tuple(sorted(self.parameters.items()))))


__all__ = [
    "StrategyDomainError",
    "StrategyDecisionType",
    "StrategyId",
    "StrategyParameter",
    "StrategyConfiguration",
    "StrategyMetadata",
    "StrategyVersion",
    "StrategySignal",
    "Signal",
    "StrategyCandidate",
    "StrategyDecision",
    "StrategyEvaluation",
    "StrategyPerformance",
    "Strategy",
    "StrategyDefinition",
    "Signal",
]
