"""Read-only, deterministic evaluation of recorded historical trading evidence."""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product
from typing import Any

from tranalyset.contracts.events import EventEnvelope
from tranalyset.plugins.paper_trading_engine.contracts import PAPER_TRADE_CLOSED


@dataclass(frozen=True, slots=True)
class ResearchPeriod:
    name: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Research period boundaries must be timezone-aware.")
        if self.start >= self.end:
            raise ValueError("Research period start must be before its end.")

    def contains(self, timestamp: datetime) -> bool:
        return self.start.astimezone(UTC) <= timestamp.astimezone(UTC) < self.end.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ResearchSplit:
    training: ResearchPeriod
    validation: ResearchPeriod
    out_of_sample: ResearchPeriod

    def __post_init__(self) -> None:
        if not (self.training.end <= self.validation.start <= self.validation.end <= self.out_of_sample.start):
            raise ValueError("Training, validation, and out-of-sample periods must be ordered and non-overlapping.")


@dataclass(frozen=True, slots=True)
class StrategyResearchInput:
    """An immutable historical run produced by the normal replay/backtest pipeline."""

    strategy_id: str
    events: Sequence[EventEnvelope]
    parameters: Mapping[str, Any]
    symbols: Sequence[str] = ()
    timeframes: Sequence[str] = ()
    regimes: Sequence[str] = ()
    sides: Sequence[str] = ("long", "short")
    strategy_version: str | None = None
    execution_assumptions: Mapping[str, Any] = field(default_factory=dict)
    dataset_identity: str | None = None


@dataclass(frozen=True, slots=True)
class LaboratoryEvaluation:
    strategy_id: str
    parameters: Mapping[str, Any]
    training: Mapping[str, float | None]
    validation: Mapping[str, float | None]
    out_of_sample: Mapping[str, float | None]
    regime_metrics: Mapping[str, Mapping[str, float | None]]
    monte_carlo: Mapping[str, float | None]
    execution_stress: Mapping[str, float | None]
    warnings: tuple[str, ...]
    status: str = "not_evaluated"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyLaboratoryReport:
    split: ResearchSplit
    rankings: tuple[LaboratoryEvaluation, ...]
    walk_forward: Mapping[str, tuple[Mapping[str, float | None], ...]]
    not_evaluated: tuple[LaboratoryEvaluation, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        def evaluation_payload(item: LaboratoryEvaluation) -> dict[str, Any]:
            return {
                "strategy_id": item.strategy_id,
                "parameters": dict(item.parameters),
                "training": dict(item.training),
                "validation": dict(item.validation),
                "out_of_sample": dict(item.out_of_sample),
                "regime_metrics": {key: dict(value) for key, value in item.regime_metrics.items()},
                "monte_carlo": dict(item.monte_carlo),
                "execution_stress": dict(item.execution_stress),
                "warnings": item.warnings,
                "status": item.status,
                "metadata": dict(item.metadata),
            }
        return {
            "training": {"start": self.split.training.start.isoformat(), "end": self.split.training.end.isoformat()},
            "validation": {"start": self.split.validation.start.isoformat(), "end": self.split.validation.end.isoformat()},
            "out_of_sample": {"start": self.split.out_of_sample.start.isoformat(), "end": self.split.out_of_sample.end.isoformat()},
            "rankings": tuple(evaluation_payload(item) for item in self.rankings),
            "not_evaluated": tuple(evaluation_payload(item) for item in self.not_evaluated),
            "walk_forward": {key: tuple(dict(value) for value in values) for key, values in self.walk_forward.items()},
        }


@dataclass(frozen=True, slots=True)
class _Trade:
    occurred_at: datetime
    pnl: float
    symbol: str
    side: str
    regime: str


class StrategyEvaluationLaboratory:
    """Pure research calculations over event evidence; it never mutates runtime services."""

    def evaluate(
        self,
        *,
        inputs: Sequence[StrategyResearchInput],
        split: ResearchSplit,
        initial_capital: float = 100_000.0,
        monte_carlo_trials: int = 200,
        execution_stress_bps: float = 5.0,
        seed: int = 0,
        walk_forward_splits: Sequence[ResearchSplit] = (),
    ) -> StrategyLaboratoryReport:
        if not math.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("initial_capital must be positive.")
        if monte_carlo_trials <= 0:
            raise ValueError("monte_carlo_trials must be positive.")
        if not math.isfinite(execution_stress_bps) or execution_stress_bps < 0:
            raise ValueError("execution_stress_bps must be finite and not negative.")
        for item in inputs:
            self._validate_input(item)
        evaluations = [
            self._evaluate_input(item, split, initial_capital, monte_carlo_trials, execution_stress_bps, seed)
            for item in inputs
        ]
        eligible = tuple(item for item in evaluations if item.status == "evaluated")
        unavailable = tuple(item for item in evaluations if item.status != "evaluated")
        ranked = tuple(sorted(eligible, key=self._ranking_key, reverse=True))
        walk_forward: dict[str, tuple[Mapping[str, float | None], ...]] = {}
        for item in inputs:
            walk_forward[item.strategy_id] = tuple(
                self._metrics(self._trades(item, candidate.out_of_sample), initial_capital)
                for candidate in walk_forward_splits
            )
        return StrategyLaboratoryReport(split=split, rankings=ranked, walk_forward=walk_forward, not_evaluated=unavailable)

    def parameter_combinations(self, parameter_grid: Mapping[str, Sequence[Any]]) -> tuple[Mapping[str, Any], ...]:
        if not parameter_grid:
            return ({},)
        names = tuple(parameter_grid)
        return tuple(dict(zip(names, values)) for values in product(*(tuple(parameter_grid[name]) for name in names)))

    def _evaluate_input(self, item: StrategyResearchInput, split: ResearchSplit, capital: float, trials: int, bps: float, seed: int) -> LaboratoryEvaluation:
        training = self._trades(item, split.training)
        validation = self._trades(item, split.validation)
        oos = self._trades(item, split.out_of_sample)
        all_trades = (*training, *validation, *oos)
        regimes: dict[str, Mapping[str, float | None]] = {}
        for regime in sorted({trade.regime for trade in all_trades}):
            regimes[regime] = self._metrics(tuple(trade for trade in all_trades if trade.regime == regime), capital)
        monte_carlo = self._monte_carlo(oos, capital, trials, seed)
        stress = self._stress()
        status = "evaluated" if training and validation and oos else "not_evaluated"
        warnings = []
        if status != "evaluated":
            warnings.append("Training, validation, and out-of-sample trades are all required for a ranked result.")
        warnings.append("Execution stress was not evaluated because complete fill-level notionals are unavailable.")
        return LaboratoryEvaluation(
            strategy_id=item.strategy_id, parameters=dict(item.parameters),
            training=self._metrics(training, capital), validation=self._metrics(validation, capital),
            out_of_sample=self._metrics(oos, capital), regime_metrics=regimes, monte_carlo=monte_carlo,
            execution_stress=stress, warnings=tuple(warnings), status=status,
            metadata=self._metadata(item, split, execution_stress_bps=bps),
        )

    def _validate_input(self, item: StrategyResearchInput) -> None:
        trade_ids: set[str] = set()
        for event in item.events:
            if event.timestamp.tzinfo is None:
                raise ValueError("Research event timestamps must be timezone-aware.")
            if str(event.event_type) != str(PAPER_TRADE_CLOSED):
                continue
            if event.source != "paper_trading_engine":
                raise ValueError("Closed-trade evidence must originate from paper_trading_engine.")
            payload = event.payload
            trade_id = _required_text(payload.get("trade_id"), "trade_id")
            if trade_id in trade_ids:
                raise ValueError(f"Duplicate closed-trade ledger entry: {trade_id}.")
            trade_ids.add(trade_id)
            if payload.get("strategy_id") != item.strategy_id:
                raise ValueError("Closed-trade strategy_id does not match the research input.")
            if _required_text(payload.get("symbol"), "symbol") == "":
                raise ValueError("Closed-trade symbol must not be empty.")
            _positive_number(payload.get("quantity"), "quantity")
            _positive_number(payload.get("average_price"), "average_price")
            _positive_number(payload.get("exit_price"), "exit_price")
            _finite_number(payload.get("realized_pnl"), "realized_pnl")
            _nonnegative_number(payload.get("commissions"), "commissions")
            _nonnegative_number(payload.get("slippage"), "slippage")
            side = _required_text(payload.get("side"), "side").lower()
            if side not in {"long", "short"}:
                raise ValueError("Closed-trade side must be 'long' or 'short'.")
            occurred = _timestamp(payload.get("occurred_at"))
            if occurred != event.timestamp.astimezone(UTC):
                raise ValueError("Closed-trade evidence timestamp must match its event timestamp.")

    def _trades(self, item: StrategyResearchInput, period: ResearchPeriod) -> tuple[_Trade, ...]:
        regimes: dict[str, str] = {}
        trades: list[_Trade] = []
        for event in sorted(item.events, key=lambda value: value.timestamp):
            timestamp = event.timestamp.astimezone(UTC)
            payload = event.payload
            symbol = str(payload.get("symbol", ""))
            if str(event.event_type) == "MarketRegimeChanged" and symbol and timestamp < period.end.astimezone(UTC):
                regimes[symbol] = str(payload.get("regime", "Unknown"))
                continue
            if str(event.event_type) != str(PAPER_TRADE_CLOSED) or not period.contains(timestamp):
                continue
            occurred = _timestamp(payload.get("occurred_at"))
            if occurred != timestamp:
                raise ValueError("Closed-trade evidence timestamp must match its event timestamp.")
            if item.symbols and symbol not in item.symbols:
                continue
            timeframe = str(payload.get("timeframe", ""))
            if item.timeframes and timeframe not in item.timeframes:
                continue
            side = str(payload.get("side", "long")).lower()
            if item.sides and side not in item.sides:
                continue
            regime = regimes.get(symbol, str(payload.get("regime", "Unknown")))
            if item.regimes and regime not in item.regimes:
                continue
            trades.append(_Trade(occurred, _number(payload.get("realized_pnl")), symbol, side, regime))
        return tuple(sorted(trades, key=lambda value: value.occurred_at))

    def _metadata(self, item: StrategyResearchInput, split: ResearchSplit, *, execution_stress_bps: float) -> Mapping[str, Any]:
        events = tuple(sorted(item.events, key=lambda value: (value.timestamp, str(value.event_type), value.source)))
        event_data = [
            {
                "source": event.source,
                "event_type": str(event.event_type),
                "timestamp": event.timestamp.isoformat(),
                "payload": dict(event.payload),
            }
            for event in events
        ]
        timestamps = [event.timestamp.astimezone(UTC) for event in events]
        return {
            "dataset_identity": item.dataset_identity or hashlib.sha256(
                json.dumps(event_data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest(),
            "date_range": {
                "start": min(timestamps).isoformat() if timestamps else None,
                "end": max(timestamps).isoformat() if timestamps else None,
            },
            "strategy_version": item.strategy_version,
            "configuration": dict(item.parameters),
            "execution_assumptions": {
                **dict(item.execution_assumptions),
                "requested_stress_bps": execution_stress_bps,
            },
            "evaluation_period": {
                "training": {"start": split.training.start.isoformat(), "end": split.training.end.isoformat()},
                "validation": {"start": split.validation.start.isoformat(), "end": split.validation.end.isoformat()},
                "out_of_sample": {"start": split.out_of_sample.start.isoformat(), "end": split.out_of_sample.end.isoformat()},
            },
        }

    def _metrics(self, trades: Sequence[_Trade], capital: float) -> Mapping[str, float | None]:
        if not trades:
            return {
                "total_return": None,
                "net_profit": None,
                "expectancy": None,
                "drawdown": None,
                "sharpe_ratio": None,
                "sortino_ratio": None,
                "profit_factor": None,
                "trade_count": 0.0,
                "win_rate": None,
            }
        pnls = [trade.pnl for trade in trades]
        equity = capital
        peak = capital
        drawdowns: list[float] = []
        returns: list[float] = []
        invalid_return = False
        for pnl in pnls:
            if equity <= 0:
                invalid_return = True
            else:
                returns.append(pnl / equity)
            equity += pnl
            peak = max(peak, equity)
            drawdowns.append((peak - equity) / peak if peak > 0 else 0.0)
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        gross_loss = abs(sum(losses))
        profit_factor = sum(wins) / gross_loss if gross_loss else (float("inf") if wins else 0.0)
        mean = statistics.mean(returns) if returns else 0.0
        deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
        downside = [value for value in returns if value < 0]
        downside_deviation = math.sqrt(sum(value * value for value in downside) / len(downside)) if downside else 0.0
        return {
            "total_return": (equity - capital) / capital,
            "net_profit": equity - capital,
            "expectancy": statistics.mean(pnls),
            "drawdown": max(drawdowns, default=0.0),
            "sharpe_ratio": None if invalid_return or len(returns) < 2 or deviation == 0 else mean / deviation,
            "sortino_ratio": None if invalid_return or len(returns) < 2 or downside_deviation == 0 else mean / downside_deviation,
            "profit_factor": profit_factor if gross_loss > 0 or wins else None,
            "trade_count": float(len(trades)),
            "win_rate": len(wins) / len(trades),
        }

    def _monte_carlo(self, trades: Sequence[_Trade], capital: float, trials: int, seed: int) -> Mapping[str, float | None]:
        if not trades:
            return {"p95_drawdown": None, "median_drawdown": None, "p05_return": None}
        rng = random.Random(seed)
        drawdowns: list[float] = []
        returns: list[float] = []
        pnls = [trade.pnl for trade in trades]
        for _ in range(trials):
            sample = list(pnls)
            rng.shuffle(sample)
            metrics = self._metrics(tuple(_Trade(datetime.min.replace(tzinfo=UTC), value, "", "", "") for value in sample), capital)
            if metrics["drawdown"] is None or metrics["total_return"] is None:
                return {"p95_drawdown": None, "median_drawdown": None, "p05_return": None}
            drawdowns.append(float(metrics["drawdown"]))
            returns.append(float(metrics["total_return"]))
        return {"p95_drawdown": _quantile(drawdowns, 0.95), "median_drawdown": _quantile(drawdowns, 0.5), "p05_return": _quantile(returns, 0.05)}

    def _stress(self) -> Mapping[str, float | None]:
        return {"total_return": None, "drawdown": None, "expectancy": None}

    def _ranking_key(self, item: LaboratoryEvaluation) -> tuple[float, float, float]:
        return (
            float(item.out_of_sample["total_return"] or 0.0),
            float(item.out_of_sample["net_profit"] or 0.0),
            float(item.validation["total_return"] or 0.0),
        )


def _number(value: object) -> float:
    return _finite_number(value, "trade metric")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Closed-trade timestamp is required.")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Historical trade timestamp must be timezone-aware.")
    return parsed.astimezone(UTC)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Closed-trade {field_name} is required.")
    return value


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise ValueError(f"Closed-trade {field_name} must be a finite number.")
    return float(value)


def _positive_number(value: object, field_name: str) -> float:
    number = _finite_number(value, field_name)
    if number <= 0:
        raise ValueError(f"Closed-trade {field_name} must be positive.")
    return number


def _nonnegative_number(value: object, field_name: str) -> float:
    number = _finite_number(value, field_name)
    if number < 0:
        raise ValueError(f"Closed-trade {field_name} must not be negative.")
    return number


def _quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))]
