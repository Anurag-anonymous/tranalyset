from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

PORTFOLIO_UPDATED = EventType("PortfolioUpdated")
EXPOSURE_CHANGED = EventType("ExposureChanged")
DRAWDOWN_CHANGED = EventType("DrawdownChanged")
CAPITAL_UPDATED = EventType("CapitalUpdated")


def _base_payload(
    *,
    portfolio_id: str,
    occurred_at: datetime,
    source_event_type: str | None,
) -> dict[str, Any]:
    require_text(portfolio_id, "portfolio_id")
    payload: dict[str, Any] = {
        "portfolio_id": portfolio_id,
        "occurred_at": to_utc_iso(occurred_at, field_name="occurred_at"),
    }
    optional_text_payload(payload, "source_event_type", source_event_type)
    return payload


@dataclass(frozen=True, slots=True)
class PortfolioUpdatedPayload:
    portfolio_id: str
    occurred_at: datetime
    capital: float
    cash: float
    open_positions: Sequence[Mapping[str, float | str]]
    closed_positions: Sequence[Mapping[str, float | str]]
    exposure: float
    sector_exposure: Mapping[str, float]
    correlation: float
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    drawdown: float
    portfolio_risk: float
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        _validate_non_negative(self.exposure, "exposure")
        _validate_ratio(self.correlation, "correlation")
        _validate_ratio(self.drawdown, "drawdown")
        _validate_ratio(self.portfolio_risk, "portfolio_risk")
        payload = _base_payload(
            portfolio_id=self.portfolio_id,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "capital": self.capital,
                "cash": self.cash,
                "open_positions": tuple(dict(position) for position in self.open_positions),
                "closed_positions": tuple(dict(position) for position in self.closed_positions),
                "exposure": self.exposure,
                "sector_exposure": dict(self.sector_exposure),
                "correlation": self.correlation,
                "daily_pnl": self.daily_pnl,
                "weekly_pnl": self.weekly_pnl,
                "monthly_pnl": self.monthly_pnl,
                "drawdown": self.drawdown,
                "portfolio_risk": self.portfolio_risk,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class ExposureChangedPayload:
    portfolio_id: str
    occurred_at: datetime
    exposure: float
    previous_exposure: float | None
    sector_exposure: Mapping[str, float] = field(default_factory=dict)
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        _validate_non_negative(self.exposure, "exposure")
        payload = _base_payload(
            portfolio_id=self.portfolio_id,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["exposure"] = self.exposure
        payload["sector_exposure"] = dict(self.sector_exposure)
        if self.previous_exposure is not None:
            _validate_non_negative(self.previous_exposure, "previous_exposure")
            payload["previous_exposure"] = self.previous_exposure
        return payload


@dataclass(frozen=True, slots=True)
class DrawdownChangedPayload:
    portfolio_id: str
    occurred_at: datetime
    drawdown: float
    previous_drawdown: float | None
    peak_capital: float
    capital: float
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        _validate_ratio(self.drawdown, "drawdown")
        _validate_non_negative(self.peak_capital, "peak_capital")
        payload = _base_payload(
            portfolio_id=self.portfolio_id,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["drawdown"] = self.drawdown
        payload["peak_capital"] = self.peak_capital
        payload["capital"] = self.capital
        if self.previous_drawdown is not None:
            _validate_ratio(self.previous_drawdown, "previous_drawdown")
            payload["previous_drawdown"] = self.previous_drawdown
        return payload


@dataclass(frozen=True, slots=True)
class CapitalUpdatedPayload:
    portfolio_id: str
    occurred_at: datetime
    capital: float
    previous_capital: float | None
    cash: float
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = _base_payload(
            portfolio_id=self.portfolio_id,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload["capital"] = self.capital
        payload["cash"] = self.cash
        payload["daily_pnl"] = self.daily_pnl
        payload["weekly_pnl"] = self.weekly_pnl
        payload["monthly_pnl"] = self.monthly_pnl
        if self.previous_capital is not None:
            payload["previous_capital"] = self.previous_capital
        return payload


def _validate_non_negative(value: float, field_name: str) -> None:
    if value < 0:
        msg = f"{field_name} must not be negative."
        raise ValueError(msg)


def _validate_ratio(value: float, field_name: str) -> None:
    if not 0.0 <= value <= 1.0:
        msg = f"{field_name} must be between 0.0 and 1.0."
        raise ValueError(msg)


PUBLISHED_EVENTS = (
    PORTFOLIO_UPDATED,
    EXPOSURE_CHANGED,
    DRAWDOWN_CHANGED,
    CAPITAL_UPDATED,
)
