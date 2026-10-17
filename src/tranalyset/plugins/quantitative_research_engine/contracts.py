from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import require_text, to_utc_iso
from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
)
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
)
from tranalyset.plugins.portfolio_engine.contracts import PORTFOLIO_UPDATED
from tranalyset.plugins.probability_engine.contracts import PROBABILITY_UPDATED
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED

RESEARCH_COMPLETED = EventType("ResearchCompleted")
STRATEGY_EVALUATED = EventType("StrategyEvaluated")
OPTIMIZATION_COMPLETED = EventType("OptimizationCompleted")

PUBLISHED_EVENTS = (
    RESEARCH_COMPLETED,
    STRATEGY_EVALUATED,
    OPTIMIZATION_COMPLETED,
)

SUBSCRIBED_EVENTS = (
    CANDLE_CLOSED,
    TICK_RECEIVED,
    TIMEFRAME_CANDLE_CLOSED,
    MARKET_CONTEXT_UPDATED,
    INDICATORS_UPDATED,
    PATTERN_DETECTED,
    PATTERN_UPDATED,
    PATTERN_INVALIDATED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    PROBABILITY_UPDATED,
    TRADE_HEALTH_UPDATED,
    PORTFOLIO_UPDATED,
)


def _base_payload(
    *,
    strategy_id: str,
    timestamp: datetime,
    source_event_type: str,
) -> dict[str, Any]:
    require_text(strategy_id, "strategy_id")
    require_text(source_event_type, "source_event_type")
    return {
        "strategy_id": strategy_id,
        "occurred_at": to_utc_iso(timestamp, field_name="occurred_at"),
        "source_event_type": source_event_type,
    }


@dataclass(frozen=True, slots=True)
class StrategyEvaluatedPayload:
    run_id: str
    strategy_id: str
    evaluated_at: datetime
    parameters: Mapping[str, Any]
    metrics: Mapping[str, float]
    robustness_score: float | None
    warnings: Sequence[str] = field(default_factory=tuple)
    source_event_type: str = "StrategyEvaluated"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    status: str = "not_evaluated"

    def to_payload(self) -> dict[str, Any]:
        require_text(self.run_id, "run_id")
        _validate_result_status(self.status, self.metadata)
        payload = _base_payload(
            strategy_id=self.strategy_id,
            timestamp=self.evaluated_at,
            source_event_type=self.source_event_type,
        )
        payload["run_id"] = self.run_id
        payload["parameters"] = dict(self.parameters)
        payload["metrics"] = dict(self.metrics) if self.status == "evaluated" else {}
        payload["robustness_score"] = None
        payload["warnings"] = tuple(self.warnings)
        payload["status"] = self.status
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class OptimizationCompletedPayload:
    run_id: str
    strategy_id: str
    completed_at: datetime
    parameter_rankings: Sequence[Mapping[str, Any]]
    best_parameters: Mapping[str, Any]
    robustness_score: float | None
    overfitting_risk: float | None
    warnings: Sequence[str] = field(default_factory=tuple)
    source_event_type: str = "OptimizationCompleted"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    status: str = "not_evaluated"

    def to_payload(self) -> dict[str, Any]:
        require_text(self.run_id, "run_id")
        _validate_result_status(self.status, self.metadata)
        payload = _base_payload(
            strategy_id=self.strategy_id,
            timestamp=self.completed_at,
            source_event_type=self.source_event_type,
        )
        payload["run_id"] = self.run_id
        payload["parameter_rankings"] = tuple(dict(item) for item in self.parameter_rankings) if self.status == "evaluated" else ()
        payload["best_parameters"] = dict(self.best_parameters) if self.status == "evaluated" else {}
        payload["robustness_score"] = None
        payload["overfitting_risk"] = None
        payload["warnings"] = tuple(self.warnings)
        payload["status"] = self.status
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class ResearchCompletedPayload:
    run_id: str
    strategy_id: str
    completed_at: datetime
    summary: str
    distributions: Mapping[str, Sequence[float]]
    warnings: Sequence[str] = field(default_factory=tuple)
    source_event_type: str = "ResearchCompleted"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    status: str = "not_evaluated"

    def to_payload(self) -> dict[str, Any]:
        require_text(self.run_id, "run_id")
        _validate_result_status(self.status, self.metadata)
        payload = _base_payload(
            strategy_id=self.strategy_id,
            timestamp=self.completed_at,
            source_event_type=self.source_event_type,
        )
        payload["run_id"] = self.run_id
        payload["summary"] = self.summary
        payload["distributions"] = (
            {str(key): tuple(value) for key, value in self.distributions.items()}
            if self.status == "evaluated"
            else {}
        )
        payload["warnings"] = tuple(self.warnings)
        payload["status"] = self.status
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def _validate_result_status(status: str, metadata: Mapping[str, Any]) -> None:
    if status not in {"evaluated", "not_evaluated"}:
        raise ValueError("Research result status must be evaluated or not_evaluated.")
    if status == "evaluated":
        required = {
            "dataset_identity",
            "date_range",
            "strategy_version",
            "configuration",
            "execution_assumptions",
            "evaluation_period",
        }
        if not required.issubset(metadata):
            raise ValueError("Evaluated research results require complete provenance metadata.")
