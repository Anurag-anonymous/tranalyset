from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from tranalyset.domain.common import AssetClass
from tranalyset.domain.common.value_objects import (
    Currency,
    Liquidity,
    Money,
    Price,
    Quantity,
    Ratio,
    Symbol,
    quantize_money,
)
from tranalyset.domain.portfolio import Position


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


class RiskDomainError(ValueError):
    pass


class StopLossType(StrEnum):
    PRICE = "price"
    PERCENT = "percent"


class TakeProfitType(StrEnum):
    PRICE = "price"
    PERCENT = "percent"


class TrailingStopType(StrEnum):
    AMOUNT = "amount"
    PERCENT = "percent"


class RiskDecisionOutcome(StrEnum):
    APPROVED = "approved"
    REDUCED = "reduced"
    REJECTED = "rejected"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class RiskEvidence:
    factors: Mapping[str, Decimal | int | float | str | bool] = field(default_factory=dict)
    description: str | None = None
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "factors", _freeze_mapping(self.factors))
        object.__setattr__(self, "sources", tuple(source.strip() for source in self.sources if source.strip()))
        if self.description is not None and not self.description.strip():
            msg = "Risk evidence description must be non-empty if provided."
            raise ValueError(msg)

    def __hash__(self) -> int:
        return hash((tuple(sorted(self.factors.items())), self.description, self.sources))


@dataclass(frozen=True, slots=True)
class DrawdownLimit:
    maximum_drawdown: Ratio
    hard_limit: bool = True

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.maximum_drawdown.value <= Decimal("1"):
            msg = "Drawdown limit must be between 0 and 1 inclusive."
            raise ValueError(msg)

    def with_limit(self, maximum_drawdown: Ratio) -> DrawdownLimit:
        return replace(self, maximum_drawdown=maximum_drawdown)


@dataclass(frozen=True, slots=True)
class ExposureLimit:
    max_exposure: Money
    symbol: Symbol | None = None
    asset_class: AssetClass | None = None

    def __post_init__(self) -> None:
        if self.max_exposure.amount < 0:
            msg = "Exposure limit must not be negative."
            raise ValueError(msg)
        if self.symbol is None and self.asset_class is None:
            msg = "Exposure limit requires a symbol or asset class."
            raise ValueError(msg)

    def matches(self, instrument: Symbol | AssetClass) -> bool:
        if isinstance(instrument, Symbol):
            return self.symbol == instrument
        return self.asset_class == instrument


@dataclass(frozen=True, slots=True)
class StopLoss:
    type: StopLossType
    price: Price | None = None
    percent: Ratio | None = None

    def __post_init__(self) -> None:
        if self.type is StopLossType.PRICE:
            if self.price is None:
                msg = "PRICE stop loss must have a price."
                raise ValueError(msg)
            if self.percent is not None:
                msg = "PRICE stop loss must not have a percent."
                raise ValueError(msg)
            if self.price.value <= 0:
                msg = "Stop loss price must be greater than zero."
                raise ValueError(msg)
        elif self.type is StopLossType.PERCENT:
            if self.percent is None:
                msg = "PERCENT stop loss must have a percent."
                raise ValueError(msg)
            if self.price is not None:
                msg = "PERCENT stop loss must not have a price."
                raise ValueError(msg)
            if not Decimal("0") < self.percent.value < Decimal("1"):
                msg = "Stop loss percent must be between 0 and 1."
                raise ValueError(msg)
        else:  # pragma: no cover - exhaustive
            raise ValueError(f"Unknown stop loss type: {self.type}")

    def copy(self, **changes: object) -> StopLoss:
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class TakeProfit:
    type: TakeProfitType
    price: Price | None = None
    percent: Ratio | None = None

    def __post_init__(self) -> None:
        if self.type is TakeProfitType.PRICE:
            if self.price is None:
                msg = "PRICE take profit must have a price."
                raise ValueError(msg)
            if self.percent is not None:
                msg = "PRICE take profit must not have a percent."
                raise ValueError(msg)
            if self.price.value <= 0:
                msg = "Take profit price must be greater than zero."
                raise ValueError(msg)
        elif self.type is TakeProfitType.PERCENT:
            if self.percent is None:
                msg = "PERCENT take profit must have a percent."
                raise ValueError(msg)
            if self.price is not None:
                msg = "PERCENT take profit must not have a price."
                raise ValueError(msg)
            if self.percent.value <= 0:
                msg = "Take profit percent must be greater than zero."
                raise ValueError(msg)
        else:  # pragma: no cover - exhaustive
            raise ValueError(f"Unknown take profit type: {self.type}")

    def copy(self, **changes: object) -> TakeProfit:
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class TrailingStop:
    type: TrailingStopType
    amount: Price | Money | None = None
    percent: Ratio | None = None
    anchor_price: Price | None = None

    def __post_init__(self) -> None:
        if self.type is TrailingStopType.AMOUNT:
            if self.amount is None:
                msg = "AMOUNT trailing stop must have an amount."
                raise ValueError(msg)
            if self.percent is not None:
                msg = "AMOUNT trailing stop must not have a percent."
                raise ValueError(msg)
        elif self.type is TrailingStopType.PERCENT:
            if self.percent is None:
                msg = "PERCENT trailing stop must have a percent."
                raise ValueError(msg)
            if self.amount is not None:
                msg = "PERCENT trailing stop must not have an amount."
                raise ValueError(msg)
            if not Decimal("0") < self.percent.value < Decimal("1"):
                msg = "Trailing stop percent must be between 0 and 1."
                raise ValueError(msg)
        else:  # pragma: no cover - exhaustive
            raise ValueError(f"Unknown trailing stop type: {self.type}")

    def copy(self, **changes: object) -> TrailingStop:
        return replace(self, **changes)

    def update_anchor(self, price: Price) -> TrailingStop:
        if self.anchor_price is None:
            return replace(self, anchor_price=price)
        if self.type is TrailingStopType.AMOUNT:
            return replace(self, anchor_price=max(self.anchor_price, price))
        return replace(self, anchor_price=max(self.anchor_price, price))


@dataclass(frozen=True, slots=True)
class PositionSizing:
    account_equity: Money
    risk_per_trade: Ratio
    entry_price: Price
    stop_loss: StopLoss
    quantity: Quantity
    risk_amount: Money
    percentage_of_equity: Ratio

    def __post_init__(self) -> None:
        if self.account_equity.amount <= 0:
            msg = "Account equity must be positive."
            raise ValueError(msg)
        if not Decimal("0") < self.risk_per_trade.value <= Decimal("1"):
            msg = "Risk per trade must be between 0 and 1."
            raise ValueError(msg)
        if self.entry_price.value <= 0:
            msg = "Entry price must be greater than zero."
            raise ValueError(msg)
        if self.quantity.value <= 0:
            msg = "Position size quantity must be greater than zero."
            raise ValueError(msg)
        if self.risk_amount.amount < 0:
            msg = "Risk amount must not be negative."
            raise ValueError(msg)
        if not Decimal("0") <= self.percentage_of_equity.value <= Decimal("1"):
            msg = "Percentage of equity must be between 0 and 1."
            raise ValueError(msg)

    def with_quantity(self, quantity: Quantity) -> PositionSizing:
        return replace(self, quantity=quantity)


@dataclass(frozen=True, slots=True)
class VolatilityRisk:
    value: Ratio
    lookback: int
    symbol: Symbol | None = None
    regime: str | None = None

    def __post_init__(self) -> None:
        if self.value.value < 0:
            msg = "Volatility risk must not be negative."
            raise ValueError(msg)
        if self.lookback <= 0:
            msg = "Volatility lookback must be positive."
            raise ValueError(msg)

    def with_value(self, value: Ratio) -> VolatilityRisk:
        return replace(self, value=value)


@dataclass(frozen=True, slots=True)
class LiquidityRisk:
    value: Ratio
    liquidity: Liquidity | None = None
    spread: Price | None = None
    symbol: Symbol | None = None

    def __post_init__(self) -> None:
        if self.value.value < 0:
            msg = "Liquidity risk must not be negative."
            raise ValueError(msg)
        if self.spread is not None and self.spread.value < 0:
            msg = "Spread must not be negative."
            raise ValueError(msg)

    def with_value(self, value: Ratio) -> LiquidityRisk:
        return replace(self, value=value)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    drawdown_limit: DrawdownLimit | None = None
    exposure_limits: tuple[ExposureLimit, ...] = ()
    stop_loss: StopLoss | None = None
    trailing_stop: TrailingStop | None = None
    take_profit: TakeProfit | None = None
    volatility_risk: VolatilityRisk | None = None
    liquidity_risk: LiquidityRisk | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "exposure_limits", tuple(self.exposure_limits))
        symbols: set[tuple[str, str]] = set()
        for exposure in self.exposure_limits:
            if exposure.symbol is not None:
                key = ("symbol", exposure.symbol.value)
                if key in symbols:
                    msg = "Exposure limits must not contain duplicate symbols."
                    raise ValueError(msg)
                symbols.add(key)
            if exposure.asset_class is not None:
                key = ("asset_class", exposure.asset_class.value)
                if key in symbols:
                    msg = "Exposure limits must not contain duplicate asset classes."
                    raise ValueError(msg)
                symbols.add(key)

    def with_limit(self, *, drawdown_limit: DrawdownLimit | None = None) -> RiskLimits:
        return replace(self, drawdown_limit=drawdown_limit if drawdown_limit is not None else self.drawdown_limit)


@dataclass(frozen=True, slots=True)
class RiskProfile:
    profile_id: str
    name: str
    currency: Currency = Currency("USD")
    max_risk_per_trade: Ratio = Ratio(0.01)
    max_portfolio_risk: Ratio = Ratio(0.05)
    max_drawdown: Ratio = Ratio(0.2)
    limits: RiskLimits = field(default_factory=RiskLimits)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            msg = "RiskProfile profile_id must not be empty."
            raise ValueError(msg)
        if not self.name.strip():
            msg = "RiskProfile name must not be empty."
            raise ValueError(msg)
        _require_aware(self.created_at, "RiskProfile created_at")
        if not Decimal("0") < self.max_risk_per_trade.value <= Decimal("1"):
            msg = "Maximum risk per trade must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") < self.max_portfolio_risk.value <= Decimal("1"):
            msg = "Maximum portfolio risk must be between 0 and 1."
            raise ValueError(msg)
        if not Decimal("0") < self.max_drawdown.value <= Decimal("1"):
            msg = "Maximum drawdown must be between 0 and 1."
            raise ValueError(msg)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def with_limits(self, limits: RiskLimits) -> RiskProfile:
        return replace(self, limits=limits)

    def __hash__(self) -> int:
        return hash(
            (
                self.profile_id,
                self.name,
                self.currency,
                self.max_risk_per_trade,
                self.max_portfolio_risk,
                self.max_drawdown,
                self.limits,
                tuple(sorted(self.metadata.items())),
                self.created_at,
            )
        )


@dataclass(frozen=True, slots=True)
class RiskScore:
    value: Decimal | int | float | str
    confidence: Ratio = Ratio(1)
    label: str | None = None
    evidence: RiskEvidence = field(default_factory=RiskEvidence)

    def __post_init__(self) -> None:
        score = _decimal(self.value)
        if not Decimal("0") <= score <= Decimal("100"):
            msg = "Risk score must be between 0 and 100."
            raise ValueError(msg)
        if not Decimal("0") <= self.confidence.value <= Decimal("1"):
            msg = "Risk score confidence must be between 0 and 1."
            raise ValueError(msg)
        if self.label is not None and not self.label.strip():
            msg = "Risk score label must be non-empty if provided."
            raise ValueError(msg)
        object.__setattr__(self, "value", score)

    @property
    def normalized(self) -> Ratio:
        return Ratio(self.value / Decimal("100"))

    def with_value(self, value: Decimal | int | float | str) -> RiskScore:
        return replace(self, value=value)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: str
    outcome: RiskDecisionOutcome
    score: RiskScore
    reason: str
    evidence: RiskEvidence = field(default_factory=RiskEvidence)
    decided_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            msg = "RiskDecision decision_id must not be empty."
            raise ValueError(msg)
        if not self.reason.strip():
            msg = "RiskDecision reason must not be empty."
            raise ValueError(msg)
        _require_aware(self.decided_at, "RiskDecision decided_at")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    trade_id: str
    approved: bool
    observed_risk: Ratio
    limits: tuple[RiskLimit, ...] = ()
    evidence: Mapping[str, Decimal | int | float | str | bool] = field(default_factory=dict)
    stop_loss: Price | None = None
    risk_profile: RiskProfile | None = None
    risk_score: RiskScore | None = None
    decision: RiskDecision | None = None
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            msg = "RiskAssessment trade_id must not be empty."
            raise ValueError(msg)
        _require_aware(self.created_at, "RiskAssessment created_at")
        object.__setattr__(self, "limits", tuple(self.limits))
        object.__setattr__(self, "evidence", _freeze_mapping(self.evidence))
        if self.approved and any(not limit.allows(self.observed_risk) for limit in self.limits):
            msg = "Approved risk assessment cannot violate supplied risk limits."
            raise ValueError(msg)
        if self.risk_score is None and self.decision is not None:
            object.__setattr__(self, "risk_score", self.decision.score)

    def with_score(self, score: RiskScore) -> RiskAssessment:
        return replace(self, risk_score=score)

    def with_decision(self, decision: RiskDecision) -> RiskAssessment:
        return replace(self, decision=decision, risk_score=decision.score)

    def __hash__(self) -> int:
        return hash(
            (
                self.trade_id,
                self.approved,
                self.observed_risk,
                self.limits,
                tuple(sorted(self.evidence.items())),
                self.stop_loss,
                self.risk_profile,
                self.risk_score,
                self.decision,
                self.created_at,
            )
        )


@dataclass(frozen=True, slots=True)
class RiskLimit:
    name: str
    max_risk: Ratio

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "RiskLimit name must not be empty."
            raise ValueError(msg)
        if not Decimal("0") <= self.max_risk.value <= Decimal("1"):
            msg = "RiskLimit max_risk must be between 0 and 1."
            raise ValueError(msg)

    def allows(self, observed_risk: Ratio) -> bool:
        return observed_risk.value <= self.max_risk.value


@dataclass(frozen=True, slots=True)
class RiskMetric:
    position: Position
    value: Decimal | int | float | str
    name: str = "risk_metric"

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "RiskMetric name must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "value", float(self.value))


@dataclass(frozen=True, slots=True)
class Exposure:
    asset_class: AssetClass | str
    amount: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = _decimal(self.amount)
        if amount < 0:
            msg = "Exposure amount must not be negative."
            raise ValueError(msg)
        object.__setattr__(self, "amount", amount)


__all__ = [
    "RiskDomainError",
    "StopLossType",
    "TakeProfitType",
    "TrailingStopType",
    "RiskDecisionOutcome",
    "RiskEvidence",
    "DrawdownLimit",
    "ExposureLimit",
    "StopLoss",
    "TakeProfit",
    "TrailingStop",
    "PositionSizing",
    "VolatilityRisk",
    "LiquidityRisk",
    "RiskLimits",
    "RiskProfile",
    "RiskScore",
    "RiskDecision",
    "RiskAssessment",
    "RiskLimit",
    "RiskMetric",
    "Exposure",
]
