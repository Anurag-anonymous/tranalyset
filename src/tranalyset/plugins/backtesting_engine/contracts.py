from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

BACKTEST_STARTED = EventType("BacktestStarted")
BACKTEST_PROGRESS_UPDATED = EventType("BacktestProgressUpdated")
BACKTEST_COMPLETED = EventType("BacktestCompleted")
BACKTEST_RESULTS_EXPORTED = EventType("BacktestResultsExported")


@dataclass(frozen=True, slots=True)
class BacktestStartedPayload:
    run_id: str
    strategy_id: str
    mode: str
    started_at: datetime
    event_count: int

    def to_payload(self) -> dict[str, Any]:
        require_text(self.run_id, "run_id")
        require_text(self.strategy_id, "strategy_id")
        require_text(self.mode, "mode")
        if self.event_count < 0:
            msg = "event_count must not be negative."
            raise ValueError(msg)
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "started_at": to_utc_iso(self.started_at, field_name="started_at"),
            "event_count": self.event_count,
        }


@dataclass(frozen=True, slots=True)
class BacktestProgressUpdatedPayload:
    run_id: str
    strategy_id: str
    mode: str
    occurred_at: datetime
    events_replayed: int
    event_count: int

    def to_payload(self) -> dict[str, Any]:
        require_text(self.run_id, "run_id")
        require_text(self.strategy_id, "strategy_id")
        require_text(self.mode, "mode")
        if self.events_replayed < 0 or self.event_count < 0:
            msg = "progress counts must not be negative."
            raise ValueError(msg)
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "occurred_at": to_utc_iso(self.occurred_at, field_name="occurred_at"),
            "events_replayed": self.events_replayed,
            "event_count": self.event_count,
        }


@dataclass(frozen=True, slots=True)
class BacktestCompletedPayload:
    run_id: str
    strategy_id: str
    mode: str
    completed_at: datetime
    metrics: Mapping[str, Any]
    equity_curve: Sequence[Mapping[str, Any]]
    daily_returns: Mapping[str, float]
    monthly_returns: Mapping[str, float]

    def to_payload(self) -> dict[str, Any]:
        require_text(self.run_id, "run_id")
        require_text(self.strategy_id, "strategy_id")
        require_text(self.mode, "mode")
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "completed_at": to_utc_iso(self.completed_at, field_name="completed_at"),
            "metrics": dict(self.metrics),
            "equity_curve": tuple(dict(point) for point in self.equity_curve),
            "daily_returns": dict(self.daily_returns),
            "monthly_returns": dict(self.monthly_returns),
        }


@dataclass(frozen=True, slots=True)
class BacktestResultsExportedPayload:
    run_id: str
    strategy_id: str
    exported_at: datetime
    export_format: str
    destination: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.run_id, "run_id")
        require_text(self.strategy_id, "strategy_id")
        require_text(self.export_format, "export_format")
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "exported_at": to_utc_iso(self.exported_at, field_name="exported_at"),
            "export_format": self.export_format,
        }
        optional_text_payload(payload, "destination", self.destination)
        return payload


PUBLISHED_EVENTS = (
    BACKTEST_STARTED,
    BACKTEST_PROGRESS_UPDATED,
    BACKTEST_COMPLETED,
    BACKTEST_RESULTS_EXPORTED,
)
