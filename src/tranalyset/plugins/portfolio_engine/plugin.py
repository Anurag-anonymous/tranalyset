from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType, ImmutableValue
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec, subscribes_to
from tranalyset.plugins.portfolio_engine.contracts import (
    CAPITAL_UPDATED,
    DRAWDOWN_CHANGED,
    EXPOSURE_CHANGED,
    PORTFOLIO_UPDATED,
    PUBLISHED_EVENTS,
    CapitalUpdatedPayload,
    DrawdownChangedPayload,
    ExposureChangedPayload,
    PortfolioUpdatedPayload,
)
from tranalyset.plugins.portfolio_engine.state import ClosedPosition, PortfolioSnapshot, PortfolioState, Position
from tranalyset.plugins.paper_trading_engine.contracts import (
    PAPER_EXECUTION_RECORDED,
    PAPER_PORTFOLIO_UPDATED,
    PAPER_TRADE_CLOSED,
)
from tranalyset.plugins.risk.contracts import RISK_APPROVED
from tranalyset.plugins.trade_manager.contracts import TRADE_OPENED


class PortfolioEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="portfolio_engine",
                name="Portfolio Engine",
                version="1.0.0",
            ),
            configuration_spec=PluginConfigurationSpec(
                (ConfigurationField("initial_capital", float, required=False, default=100_000.0),)
            ),
        )
        self._state = PortfolioState()
        self._last_snapshot: PortfolioSnapshot | None = None
        self._pending_risk: dict[str, float] = {}
        self._processed_snapshots: set[str] = set()
        self._event_counts = {
            str(PAPER_PORTFOLIO_UPDATED): 0,
            str(PAPER_TRADE_CLOSED): 0,
            str(TRADE_OPENED): 0,
            str(RISK_APPROVED): 0,
        }

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Portfolio engine plugin publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        initial_capital = self.configuration.values["initial_capital"]
        self._state = PortfolioState(
            initial_capital=initial_capital,
            cash=initial_capital,
            peak_capital=initial_capital,
        )

    async def on_start(self) -> None:
        event = EventEnvelope.create(
            source=self.metadata.plugin_id,
            event_type=PORTFOLIO_UPDATED,
            payload={},
        )
        await self._publish_state(event)

    @subscribes_to(PAPER_PORTFOLIO_UPDATED)
    async def handle_paper_portfolio_updated(self, event: EventEnvelope) -> None:
        if self._seen_snapshot(event):
            return
        self._count(event.event_type)
        payload = event.payload
        initial_cash = _required_number(payload.get("initial_cash", self._state.initial_capital), "initial_cash")
        self._state.initial_capital = initial_cash
        self._state.cash = _required_number(payload.get("cash"), "cash")
        self._state.peak_capital = _required_number(payload.get("peak_equity", initial_cash), "peak_equity")
        positions: dict[str, Position] = {}
        for raw in _sequence(payload.get("virtual_positions")):
            item = _mapping(raw)
            trade_id = _text(item.get("trade_id"), default="")
            symbol = _text(item.get("symbol"), default="")
            if not trade_id or not symbol:
                raise ValueError("Paper position snapshots require trade_id and symbol.")
            quantity = _required_number(item.get("quantity"), "position quantity")
            entry_price = _required_number(item.get("average_price"), "position average_price")
            current_price = _required_number(item.get("last_price"), "position last_price")
            if quantity <= 0 or entry_price <= 0 or current_price <= 0:
                raise ValueError("Paper position quantity and prices must be positive.")
            positions[trade_id] = Position(
                trade_id=trade_id,
                symbol=symbol,
                quantity=quantity,
                entry_price=entry_price,
                current_price=current_price,
                sector=_text(item.get("sector"), default="Unclassified"),
                correlation_group=_text(item.get("correlation_group"), default="Unclassified"),
                realized_pnl=_required_number(item.get("realized_pnl", 0.0), "position realized_pnl"),
            )
        reported_equity = _required_number(payload.get("equity"), "equity")
        reconstructed_equity = self._state.cash + sum(position.market_value for position in positions.values())
        if abs(reported_equity - reconstructed_equity) > 0.001:
            raise ValueError("Paper portfolio equity does not reconcile to cash and filled positions.")
        self._state.open_positions = positions
        is_initial_paper_snapshot = (
            event.source == "paper_trading_engine"
            and payload.get("source_event_type") == str(PAPER_EXECUTION_RECORDED)
            and not self._state.equity_history
            and not positions
            and not _sequence(payload.get("trade_history"))
            and abs(reported_equity - initial_cash) <= 0.001
        )
        if not is_initial_paper_snapshot:
            self._state.record_equity(event.timestamp, reported_equity)
        for trade_id in positions:
            if trade_id in self._pending_risk:
                self._state.risk_by_trade[trade_id] = self._pending_risk.pop(trade_id)
        closed: list[ClosedPosition] = []
        for raw in _sequence(payload.get("trade_history")):
            item = _mapping(raw)
            closed.append(
                ClosedPosition(
                    trade_id=_text(item.get("trade_id"), default=""),
                    symbol=_text(item.get("symbol"), default=""),
                    quantity=_required_number(item.get("quantity"), "closed quantity"),
                    entry_price=_required_number(item.get("average_price"), "closed average_price"),
                    exit_price=_required_number(item.get("exit_price"), "closed exit_price"),
                    realized_pnl=_required_number(item.get("realized_pnl"), "closed realized_pnl"),
                    sector=_text(item.get("sector"), default="Unclassified"),
                )
            )
        self._state.closed_positions = closed
        await self._publish_state(event)

    @subscribes_to(PAPER_TRADE_CLOSED)
    async def handle_paper_trade_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        trade_id = _text(event.payload.get("trade_id"), default="")
        if self._state.risk_by_trade.pop(trade_id, None) is not None:
            await self._publish_state(event)

    @subscribes_to(TRADE_OPENED)
    async def handle_trade_opened(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        trade_id = _text(event.payload.get("trade_id"), default="")
        if not trade_id or trade_id not in self._state.open_positions:
            return
        self._state.risk_by_trade[trade_id] = self._pending_risk.pop(trade_id, 0.0)
        await self._publish_state(event)

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        trade_id = _text(event.payload.get("trade_id"), default="")
        if not trade_id:
            return
        evidence = _mapping(event.payload.get("evidence", {}))
        risk_amount = _number(evidence.get("risk_amount"), default=0.0)
        if trade_id not in self._state.risk_by_trade:
            self._pending_risk[trade_id] = risk_amount

    async def publish_portfolio_updated(self, payload: PortfolioUpdatedPayload) -> None:
        await self.publish(PORTFOLIO_UPDATED, payload=payload.to_payload())

    async def publish_exposure_changed(self, payload: ExposureChangedPayload) -> None:
        await self.publish(EXPOSURE_CHANGED, payload=payload.to_payload())

    async def publish_drawdown_changed(self, payload: DrawdownChangedPayload) -> None:
        await self.publish(DRAWDOWN_CHANGED, payload=payload.to_payload())

    async def publish_capital_updated(self, payload: CapitalUpdatedPayload) -> None:
        await self.publish(CAPITAL_UPDATED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if not any(self._event_counts.values()):
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Portfolio engine is running before portfolio evidence.",
                details=dict(self._event_counts),
            )
        snapshot = self._state.snapshot()
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Portfolio engine is tracking portfolio state.",
            details={
                **self._event_counts,
                "open_positions": len(snapshot.open_positions),
                "closed_positions": len(snapshot.closed_positions),
                "capital": snapshot.capital,
                "portfolio_risk": snapshot.portfolio_risk,
            },
        )

    def current_snapshot(self) -> PortfolioSnapshot:
        return self._state.snapshot()

    async def _publish_state(self, event: EventEnvelope) -> None:
        previous = self._last_snapshot
        current = self._state.snapshot()
        self._last_snapshot = current
        await self.publish_portfolio_updated(
            PortfolioUpdatedPayload(
                portfolio_id="primary",
                occurred_at=event.timestamp,
                capital=current.capital,
                cash=current.cash,
                open_positions=current.open_positions,
                closed_positions=current.closed_positions,
                exposure=current.exposure,
                sector_exposure=current.sector_exposure,
                correlation=current.correlation,
                daily_pnl=current.daily_pnl,
                weekly_pnl=current.weekly_pnl,
                monthly_pnl=current.monthly_pnl,
                drawdown=current.drawdown,
                portfolio_risk=current.portfolio_risk,
                source_event_type=str(event.event_type),
            )
        )
        if previous is None or previous.exposure != current.exposure:
            await self.publish_exposure_changed(
                ExposureChangedPayload(
                    portfolio_id="primary",
                    occurred_at=event.timestamp,
                    exposure=current.exposure,
                    previous_exposure=None if previous is None else previous.exposure,
                    sector_exposure=current.sector_exposure,
                    source_event_type=str(event.event_type),
                )
            )
        if previous is None or previous.drawdown != current.drawdown:
            await self.publish_drawdown_changed(
                DrawdownChangedPayload(
                    portfolio_id="primary",
                    occurred_at=event.timestamp,
                    drawdown=current.drawdown,
                    previous_drawdown=None if previous is None else previous.drawdown,
                    peak_capital=current.peak_capital,
                    capital=current.capital,
                    source_event_type=str(event.event_type),
                )
            )
        if previous is None or previous.capital != current.capital or previous.cash != current.cash:
            await self.publish_capital_updated(
                CapitalUpdatedPayload(
                    portfolio_id="primary",
                    occurred_at=event.timestamp,
                    capital=current.capital,
                    previous_capital=None if previous is None else previous.capital,
                    cash=current.cash,
                    daily_pnl=current.daily_pnl,
                    weekly_pnl=current.weekly_pnl,
                    monthly_pnl=current.monthly_pnl,
                    source_event_type=str(event.event_type),
                )
            )

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1

    def _seen_snapshot(self, event: EventEnvelope) -> bool:
        payload = json.dumps(
            {
                "timestamp": event.timestamp.isoformat(),
                "payload": _plain_mapping(event.payload),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if key in self._processed_snapshots:
            return True
        self._processed_snapshots.add(key)
        return False


def _mapping(value: ImmutableValue | object) -> dict[str, Any]:
    if hasattr(value, "items"):
        return dict(value.items())  # type: ignore[union-attr]
    return {}


def _sequence(value: ImmutableValue | object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, tuple | list) else ()


def _required_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite.")
    return number


def _plain_mapping(value: object) -> object:
    if hasattr(value, "items"):
        return {str(key): _plain_mapping(item) for key, item in value.items()}  # type: ignore[union-attr]
    if isinstance(value, tuple | list):
        return tuple(_plain_mapping(item) for item in value)
    return value


def _number(value: ImmutableValue | object, *, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    return default


def _text(value: ImmutableValue | object, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def create_plugin() -> PortfolioEnginePlugin:
    return PortfolioEnginePlugin()
