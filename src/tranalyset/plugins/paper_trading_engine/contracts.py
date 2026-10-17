from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

PAPER_TRADE_UPDATED = EventType("PaperTradeUpdated")
PAPER_TRADE_CLOSED = EventType("PaperTradeClosed")
PAPER_PORTFOLIO_UPDATED = EventType("PaperPortfolioUpdated")
PAPER_EXECUTION_RECORDED = EventType("PaperExecutionRecorded")
PAPER_PERFORMANCE_REPORTED = EventType("PaperPerformanceReported")
PAPER_RISK_LIMIT_ENFORCED = EventType("PaperRiskLimitEnforced")


def _base_payload(
    *,
    trade_id: str,
    symbol: str,
    occurred_at: datetime,
    source_event_type: str | None,
) -> dict[str, Any]:
    require_text(trade_id, "trade_id")
    require_text(symbol, "symbol")
    payload: dict[str, Any] = {
        "trade_id": trade_id,
        "symbol": symbol,
        "occurred_at": to_utc_iso(occurred_at, field_name="occurred_at"),
    }
    optional_text_payload(payload, "source_event_type", source_event_type)
    return payload


@dataclass(frozen=True, slots=True)
class PaperTradeUpdatedPayload:
    trade_id: str
    symbol: str
    occurred_at: datetime
    status: str
    quantity: float
    average_price: float
    last_price: float
    realized_pnl: float
    unrealized_pnl: float
    commissions: float
    slippage: float
    order_type: str
    source_event_type: str | None = None
    strategy_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.status, "status")
        require_text(self.order_type, "order_type")
        payload = _base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "status": self.status,
                "quantity": self.quantity,
                "average_price": self.average_price,
                "last_price": self.last_price,
                "realized_pnl": self.realized_pnl,
                "unrealized_pnl": self.unrealized_pnl,
                "commissions": self.commissions,
                "slippage": self.slippage,
                "order_type": self.order_type,
            }
        )
        optional_text_payload(payload, "strategy_id", self.strategy_id)
        return payload


@dataclass(frozen=True, slots=True)
class PaperTradeClosedPayload:
    trade_id: str
    symbol: str
    occurred_at: datetime
    quantity: float
    average_price: float
    exit_price: float
    realized_pnl: float
    commissions: float
    slippage: float
    reason: str
    source_event_type: str | None = None
    strategy_id: str | None = None
    side: str = "long"

    def to_payload(self) -> dict[str, Any]:
        require_text(self.reason, "reason")
        if self.side.lower() != "long":
            raise ValueError("The paper execution engine currently supports long trades only.")
        payload = _base_payload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            occurred_at=self.occurred_at,
            source_event_type=self.source_event_type,
        )
        payload.update(
            {
                "quantity": self.quantity,
                "average_price": self.average_price,
                "exit_price": self.exit_price,
                "realized_pnl": self.realized_pnl,
                "commissions": self.commissions,
                "slippage": self.slippage,
                "reason": self.reason,
                "side": self.side,
            }
        )
        optional_text_payload(payload, "strategy_id", self.strategy_id)
        return payload


@dataclass(frozen=True, slots=True)
class PaperPortfolioUpdatedPayload:
    portfolio_id: str
    occurred_at: datetime
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    commissions: float
    slippage: float
    virtual_positions: Sequence[Mapping[str, float | str]]
    virtual_orders: Sequence[Mapping[str, float | str]]
    trade_history: Sequence[Mapping[str, float | str]]
    source_event_type: str | None = None
    initial_cash: float | None = None
    peak_equity: float | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.portfolio_id, "portfolio_id")
        payload: dict[str, Any] = {
            "portfolio_id": self.portfolio_id,
            "occurred_at": to_utc_iso(self.occurred_at, field_name="occurred_at"),
            "cash": self.cash,
            "equity": self.equity,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "commissions": self.commissions,
            "slippage": self.slippage,
            "virtual_positions": tuple(dict(position) for position in self.virtual_positions),
            "virtual_orders": tuple(dict(order) for order in self.virtual_orders),
            "trade_history": tuple(dict(trade) for trade in self.trade_history),
        }
        if self.initial_cash is not None:
            payload["initial_cash"] = self.initial_cash
        if self.peak_equity is not None:
            payload["peak_equity"] = self.peak_equity
        optional_text_payload(payload, "source_event_type", self.source_event_type)
        return payload


@dataclass(frozen=True, slots=True)
class PaperExecutionRecordedPayload:
    occurred_at: datetime
    action: str
    trade_id: str | None = None
    order_id: str | None = None
    symbol: str | None = None
    reason: str | None = None
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    execution_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.action, "action")
        payload: dict[str, Any] = {"occurred_at": to_utc_iso(self.occurred_at, field_name="occurred_at"), "action": self.action, "evidence": dict(self.evidence)}
        optional_text_payload(payload, "trade_id", self.trade_id)
        optional_text_payload(payload, "order_id", self.order_id)
        optional_text_payload(payload, "symbol", self.symbol)
        optional_text_payload(payload, "reason", self.reason)
        optional_text_payload(payload, "execution_id", self.execution_id)
        return payload


@dataclass(frozen=True, slots=True)
class PaperPerformanceReportedPayload:
    occurred_at: datetime
    period: str
    metrics: Mapping[str, float]

    def to_payload(self) -> dict[str, Any]:
        require_text(self.period, "period")
        return {"occurred_at": to_utc_iso(self.occurred_at, field_name="occurred_at"), "period": self.period, "metrics": dict(self.metrics)}


PUBLISHED_EVENTS = (
    PAPER_TRADE_UPDATED,
    PAPER_TRADE_CLOSED,
    PAPER_PORTFOLIO_UPDATED,
    PAPER_EXECUTION_RECORDED,
    PAPER_PERFORMANCE_REPORTED,
    PAPER_RISK_LIMIT_ENFORCED,
)
