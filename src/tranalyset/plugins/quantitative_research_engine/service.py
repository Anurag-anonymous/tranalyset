from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import product
from typing import Any
from uuid import uuid4

from tranalyset.contracts.events import EventEnvelope
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.market_data.replay import ReplayEngine
from tranalyset.plugins.quantitative_research_engine.laboratory import (
    StrategyEvaluationLaboratory,
    StrategyLaboratoryReport,
    StrategyResearchInput,
    ResearchSplit,
)


@dataclass(frozen=True, slots=True)
class StrategyEvaluationResult:
    run_id: str
    strategy_id: str
    evaluated_at: datetime
    parameters: Mapping[str, Any]
    metrics: Mapping[str, float]
    robustness_score: float | None
    warnings: Sequence[str]
    status: str = "not_evaluated"
    metadata: Mapping[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "parameters": dict(self.parameters),
            "metrics": dict(self.metrics),
            "robustness_score": None,
            "warnings": tuple(self.warnings),
            "status": self.status,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    run_id: str
    strategy_id: str
    completed_at: datetime
    parameter_rankings: tuple[Mapping[str, Any], ...]
    best_parameters: Mapping[str, Any]
    robustness_score: float | None
    overfitting_risk: float | None
    warnings: Sequence[str]
    status: str = "not_evaluated"
    metadata: Mapping[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "completed_at": self.completed_at.isoformat(),
            "parameter_rankings": tuple(dict(item) for item in self.parameter_rankings),
            "best_parameters": dict(self.best_parameters),
            "robustness_score": None,
            "overfitting_risk": None,
            "warnings": tuple(self.warnings),
            "status": self.status,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True, slots=True)
class ResearchReport:
    run_id: str
    strategy_id: str
    completed_at: datetime
    summary: str
    distributions: Mapping[str, Sequence[float]]
    warnings: Sequence[str]
    status: str = "not_evaluated"
    metadata: Mapping[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "completed_at": self.completed_at.isoformat(),
            "summary": self.summary,
            "distributions": {str(key): tuple(value) for key, value in self.distributions.items()},
            "warnings": tuple(self.warnings),
            "status": self.status,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True, slots=True)
class QuantitativeResearchSettings:
    baseline_periods: int = 5
    monte_carlo_trials: int = 50
    bootstrap_trials: int = 50
    sensitivity_delta: float = 0.05
    stress_threshold: float = 0.75

    def __post_init__(self) -> None:
        if self.baseline_periods <= 0:
            raise ValueError("baseline_periods must be positive.")
        if self.monte_carlo_trials <= 0:
            raise ValueError("monte_carlo_trials must be positive.")
        if self.bootstrap_trials <= 0:
            raise ValueError("bootstrap_trials must be positive.")
        if self.sensitivity_delta < 0.0:
            raise ValueError("sensitivity_delta must not be negative.")
        if not 0.0 <= self.stress_threshold <= 1.0:
            raise ValueError("stress_threshold must be between 0.0 and 1.0.")


class QuantitativeResearchService:
    def __init__(
        self,
        *,
        repository: MarketDataRepository,
        replay_engine: ReplayEngine,
        indicator_cache: IndicatorCache,
        aggregation_engine: TimeframeAggregationEngine,
        settings: QuantitativeResearchSettings | None = None,
    ) -> None:
        self._repository = repository
        self._replay_engine = replay_engine
        self._indicator_cache = indicator_cache
        self._aggregation_engine = aggregation_engine
        self._settings = settings or QuantitativeResearchSettings()
        self._experiments: dict[str, ResearchReport] = {}
        self._laboratory = StrategyEvaluationLaboratory()

    def evaluate_laboratory(
        self,
        *,
        inputs: Sequence[StrategyResearchInput],
        split: ResearchSplit,
        initial_capital: float = 100_000.0,
        monte_carlo_trials: int | None = None,
        execution_stress_bps: float = 5.0,
        seed: int = 0,
        walk_forward_splits: Sequence[ResearchSplit] = (),
    ) -> StrategyLaboratoryReport:
        """Evaluate immutable replay evidence without publishing or changing runtime state."""
        return self._laboratory.evaluate(
            inputs=inputs,
            split=split,
            initial_capital=initial_capital,
            monte_carlo_trials=(
                monte_carlo_trials
                if monte_carlo_trials is not None
                else self._settings.monte_carlo_trials
            ),
            execution_stress_bps=execution_stress_bps,
            seed=seed,
            walk_forward_splits=walk_forward_splits,
        )

    def evaluate_strategy(
        self,
        *,
        strategy_id: str,
        parameters: Mapping[str, Any],
        event_windows: Sequence[Sequence[EventEnvelope]],
        run_id: str | None = None,
    ) -> StrategyEvaluationResult:
        actual_run_id = run_id or str(uuid4())
        evaluated_at = datetime.now(UTC)
        return StrategyEvaluationResult(
            run_id=actual_run_id,
            strategy_id=strategy_id,
            evaluated_at=evaluated_at,
            parameters=dict(parameters),
            metrics={},
            robustness_score=None,
            warnings=("No strategy-specific historical simulation result is available; evaluation was not performed.",),
            status="not_evaluated",
            metadata=self._result_metadata(event_windows, configuration=parameters),
        )

    def run_parameter_sweep(
        self,
        *,
        strategy_id: str,
        parameter_grid: Mapping[str, Sequence[Any]],
        event_windows: Sequence[Sequence[EventEnvelope]],
        run_id: str | None = None,
    ) -> OptimizationResult:
        actual_run_id = run_id or str(uuid4())
        combinations = self._cartesian_parameters(parameter_grid)
        if not combinations:
            raise ValueError("parameter_grid must contain at least one parameter set.")
        return OptimizationResult(
            run_id=actual_run_id,
            strategy_id=strategy_id,
            completed_at=datetime.now(UTC),
            parameter_rankings=(),
            best_parameters={},
            robustness_score=None,
            overfitting_risk=None,
            warnings=("Parameter configurations were not backtested; no ranking was produced.",),
            status="not_evaluated",
            metadata=self._result_metadata(
                event_windows,
                configuration={"parameter_grid": {name: tuple(values) for name, values in parameter_grid.items()}},
            ),
        )

    def run_walk_forward(
        self,
        *,
        strategy_id: str,
        event_windows: Sequence[Sequence[EventEnvelope]],
        parameter_set: Mapping[str, Any],
    ) -> tuple[StrategyEvaluationResult, ...]:
        return tuple(
            self.evaluate_strategy(
                strategy_id=strategy_id,
                parameters=parameter_set,
                event_windows=(window,),
            )
            for window in event_windows
        )

    def run_monte_carlo(
        self,
        *,
        strategy_id: str,
        base_events: Sequence[EventEnvelope],
        trials: int | None = None,
        seed: int | None = None,
    ) -> ResearchReport:
        return self._unavailable_report(
            strategy_id,
            "Monte Carlo analysis requires completed simulated trade results.",
            base_events,
        )

    def run_bootstrap(
        self,
        *,
        strategy_id: str,
        base_events: Sequence[EventEnvelope],
        trials: int | None = None,
        seed: int | None = None,
    ) -> ResearchReport:
        return self._unavailable_report(
            strategy_id,
            "Bootstrap analysis requires completed simulated trade results.",
            base_events,
        )

    def run_sensitivity_analysis(
        self,
        *,
        strategy_id: str,
        base_parameters: Mapping[str, Any],
        event_windows: Sequence[Sequence[EventEnvelope]],
    ) -> ResearchReport:
        return self._unavailable_report(
            strategy_id,
            "Sensitivity analysis requires a backtest for each perturbed configuration.",
            tuple(event for window in event_windows for event in window),
            configuration=base_parameters,
        )

    def run_cross_validation(
        self,
        *,
        strategy_id: str,
        parameter_set: Mapping[str, Any],
        folds: Sequence[Sequence[EventEnvelope]],
    ) -> tuple[StrategyEvaluationResult, ...]:
        return tuple(
            StrategyEvaluationResult(
                run_id=str(uuid4()),
                strategy_id=strategy_id,
                evaluated_at=datetime.now(UTC),
                parameters=dict(parameter_set),
                metrics={},
                robustness_score=None,
                warnings=("No fold-specific strategy simulation result is available.",),
                status="not_evaluated",
                metadata=self._result_metadata((fold,), configuration=parameter_set),
            )
            for fold in folds
        )

    def run_stress_test(
        self,
        *,
        strategy_id: str,
        scenario_definitions: Sequence[Mapping[str, Any]],
        event_windows: Sequence[Sequence[EventEnvelope]],
    ) -> ResearchReport:
        return self._unavailable_report(
            strategy_id,
            "Stress scenarios were not applied to a strategy backtest.",
            tuple(event for window in event_windows for event in window),
            configuration={"scenarios": tuple(dict(item) for item in scenario_definitions)},
        )

    def export_result(self, report: ResearchReport, *, export_format: str) -> str:
        if export_format == "json":
            return json.dumps(report.to_payload(), sort_keys=True)
        if export_format == "csv":
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(("run_id", report.run_id))
            writer.writerow(("strategy_id", report.strategy_id))
            writer.writerow(("completed_at", report.completed_at.isoformat()))
            writer.writerow(("summary", report.summary))
            for name, values in report.distributions.items():
                writer.writerow((name, *values))
            for warning in report.warnings:
                writer.writerow(("warning", warning))
            return output.getvalue()
        raise ValueError(f"Unsupported export format: {export_format}")

    def _cartesian_parameters(self, grid: Mapping[str, Sequence[Any]]) -> list[Mapping[str, Any]]:
        if not grid:
            return []
        names = list(grid)
        values = [tuple(grid[name]) for name in names]
        return [dict(zip(names, combination)) for combination in product(*values)]

    def _unavailable_report(
        self,
        strategy_id: str,
        reason: str,
        events: Sequence[EventEnvelope],
        *,
        configuration: Mapping[str, Any] | None = None,
    ) -> ResearchReport:
        report = ResearchReport(
            run_id=str(uuid4()),
            strategy_id=strategy_id,
            completed_at=datetime.now(UTC),
            summary=f"Not evaluated: {reason}",
            distributions={},
            warnings=(reason,),
            metadata=self._result_metadata((events,), configuration=configuration or {}),
        )
        self._experiments[report.run_id] = report
        return report

    @staticmethod
    def _result_metadata(
        event_windows: Sequence[Sequence[EventEnvelope]],
        *,
        configuration: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        events = tuple(event for window in event_windows for event in window)
        serialized = json.dumps(
            [
                {
                    "source": event.source,
                    "event_type": str(event.event_type),
                    "timestamp": event.timestamp.isoformat(),
                    "payload": dict(event.payload),
                }
                for event in events
            ],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        timestamps = [event.timestamp.astimezone(UTC) for event in events if event.timestamp.tzinfo is not None]
        date_range = {
            "start": min(timestamps).isoformat() if timestamps else None,
            "end": max(timestamps).isoformat() if timestamps else None,
        }
        return {
            "dataset_identity": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "date_range": date_range,
            "strategy_version": None,
            "configuration": dict(configuration),
            "execution_assumptions": None,
            "evaluation_period": dict(date_range),
        }
