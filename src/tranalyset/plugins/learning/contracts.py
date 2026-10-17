from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

LEARNING_UPDATED = EventType("LearningUpdated")
STRATEGY_IMPROVED = EventType("StrategyImproved")
LEARNING_INSIGHTS_GENERATED = EventType("LearningInsightsGenerated")
STRATEGY_PERFORMANCE_UPDATED = EventType("StrategyPerformanceUpdated")


@dataclass(frozen=True, slots=True)
class LearningUpdatedPayload:
    model_name: str
    updated_at: datetime
    observations_processed: int
    metrics: Mapping[str, float] = field(default_factory=dict)
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.model_name, "model_name")
        if self.observations_processed < 0:
            msg = "observations_processed must not be negative."
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "model_name": self.model_name,
            "updated_at": to_utc_iso(self.updated_at, field_name="updated_at"),
            "observations_processed": self.observations_processed,
            "metrics": dict(self.metrics),
        }
        optional_text_payload(payload, "source_event_type", self.source_event_type)
        return payload


@dataclass(frozen=True, slots=True)
class StrategyImprovedPayload:
    strategy_id: str
    improved_at: datetime
    improvement_summary: str
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    confidence: float | None = None
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.strategy_id, "strategy_id")
        require_text(self.improvement_summary, "improvement_summary")
        payload: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "improved_at": to_utc_iso(self.improved_at, field_name="improved_at"),
            "improvement_summary": self.improvement_summary,
            "evidence": dict(self.evidence),
        }
        if self.confidence is not None:
            if not 0.0 <= self.confidence <= 1.0:
                msg = "confidence must be between 0.0 and 1.0."
                raise ValueError(msg)
            payload["confidence"] = self.confidence
        optional_text_payload(payload, "source_event_type", self.source_event_type)
        return payload


@dataclass(frozen=True, slots=True)
class LearningInsightsGeneratedPayload:
    generated_at: datetime
    best_setups: tuple[Mapping[str, Any], ...]
    worst_setups: tuple[Mapping[str, Any], ...]
    optimal_holding_time_hours: float
    optimal_rr: float
    common_mistakes: tuple[str, ...]
    learning_insights: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        if self.optimal_holding_time_hours < 0:
            msg = "optimal_holding_time_hours must not be negative."
            raise ValueError(msg)
        if self.optimal_rr < 0:
            msg = "optimal_rr must not be negative."
            raise ValueError(msg)
        return {
            "generated_at": to_utc_iso(self.generated_at, field_name="generated_at"),
            "best_setups": [dict(setup) for setup in self.best_setups],
            "worst_setups": [dict(setup) for setup in self.worst_setups],
            "optimal_holding_time_hours": self.optimal_holding_time_hours,
            "optimal_rr": self.optimal_rr,
            "common_mistakes": tuple(self.common_mistakes),
            "learning_insights": tuple(self.learning_insights),
        }


@dataclass(frozen=True, slots=True)
class StrategyPerformanceUpdatedPayload:
    updated_at: datetime
    win_rate: float
    average_reward_risk: float
    average_holding_time_hours: float
    best_regime: str | None = None
    best_pattern: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if not 0.0 <= self.win_rate <= 1.0:
            msg = "win_rate must be between 0.0 and 1.0."
            raise ValueError(msg)
        if self.average_reward_risk < 0:
            msg = "average_reward_risk must not be negative."
            raise ValueError(msg)
        if self.average_holding_time_hours < 0:
            msg = "average_holding_time_hours must not be negative."
            raise ValueError(msg)
        payload: dict[str, Any] = {
            "updated_at": to_utc_iso(self.updated_at, field_name="updated_at"),
            "win_rate": self.win_rate,
            "average_reward_risk": self.average_reward_risk,
            "average_holding_time_hours": self.average_holding_time_hours,
        }
        optional_text_payload(payload, "best_regime", self.best_regime)
        optional_text_payload(payload, "best_pattern", self.best_pattern)
        return payload


PUBLISHED_EVENTS = (
    LEARNING_UPDATED,
    STRATEGY_IMPROVED,
    LEARNING_INSIGHTS_GENERATED,
    STRATEGY_PERFORMANCE_UPDATED,
)
