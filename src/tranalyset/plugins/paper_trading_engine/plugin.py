from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType, ImmutableValue
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec, subscribes_to
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED
from tranalyset.plugins.paper_trading_engine.contracts import (
    PAPER_PORTFOLIO_UPDATED,
    PAPER_EXECUTION_RECORDED,
    PAPER_PERFORMANCE_REPORTED,
    PAPER_RISK_LIMIT_ENFORCED,
    PAPER_TRADE_CLOSED,
    PAPER_TRADE_UPDATED,
    PUBLISHED_EVENTS,
    PaperPortfolioUpdatedPayload,
    PaperExecutionRecordedPayload,
    PaperPerformanceReportedPayload,
    PaperTradeClosedPayload,
    PaperTradeUpdatedPayload,
)
from tranalyset.plugins.paper_trading_engine.state import PaperTradingState, VirtualOrder, VirtualPosition
from tranalyset.plugins.risk.contracts import (
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
    TAKE_PROFIT_UPDATED,
    TRAILING_STOP_UPDATED,
    TRADE_REQUESTED,
)
from tranalyset.plugins.trade_manager.contracts import (
    TRADE_CLOSED,
    TRADE_MODIFIED,
    TRADE_OPENED,
    TradeClosedPayload,
    TradeModifiedPayload,
    TradeOpenedPayload,
)

ACCEPTED_CANDIDATE = EventType("AcceptedCandidate")
REJECTED_CANDIDATE = EventType("RejectedCandidate")


class PaperTradingEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="paper_trading_engine",
                name="Paper Trading Engine",
                version="1.0.0",
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("initial_cash", float, required=False, default=100_000.0),
                    ConfigurationField("commission_rate", float, required=False, default=0.001),
                    ConfigurationField("slippage_rate", float, required=False, default=0.0005),
                    ConfigurationField("latency_ms", int, required=False, default=0),
                    ConfigurationField("max_order_participation", float, required=False, default=1.0),
                    ConfigurationField("liquidity_constrained", bool, required=False, default=False),
                    ConfigurationField("max_positions", int, required=False, default=20),
                    ConfigurationField("max_exposure", float, required=False, default=1.0),
                    ConfigurationField("max_daily_loss", float, required=False, default=0.05),
                    ConfigurationField("max_drawdown", float, required=False, default=0.20),
                )
            ),
        )
        self._state = PaperTradingState()
        self._commission_rate = 0.001
        self._slippage_rate = 0.0005
        self._latency = timedelta(0)
        self._max_order_participation = 1.0
        self._liquidity_constrained = False
        self._max_positions = 20
        self._max_exposure = 1.0
        self._max_daily_loss = 0.05
        self._max_drawdown = 0.20
        self._event_counts = {
            str(TRADE_OPENED): 0,
            str(TRADE_MODIFIED): 0,
            str(TRADE_CLOSED): 0,
            str(TICK_RECEIVED): 0,
            str(CANDLE_CLOSED): 0,
            str(STOP_LOSS_UPDATED): 0,
            str(TAKE_PROFIT_UPDATED): 0,
            str(TRAILING_STOP_UPDATED): 0,
            str(ACCEPTED_CANDIDATE): 0,
            str(REJECTED_CANDIDATE): 0,
            str(RISK_APPROVED): 0,
            str(RISK_REJECTED): 0,
        }

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Paper trading engine plugin publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        initial_cash = self.configuration.values["initial_cash"]
        self._commission_rate = self.configuration.values["commission_rate"]
        self._slippage_rate = self.configuration.values["slippage_rate"]
        self._latency = timedelta(milliseconds=self.configuration.values["latency_ms"])
        self._max_order_participation = self.configuration.values["max_order_participation"]
        self._liquidity_constrained = self.configuration.values["liquidity_constrained"]
        self._max_positions = self.configuration.values["max_positions"]
        self._max_exposure = self.configuration.values["max_exposure"]
        self._max_daily_loss = self.configuration.values["max_daily_loss"]
        self._max_drawdown = self.configuration.values["max_drawdown"]
        self._state = PaperTradingState(initial_cash=initial_cash, cash=initial_cash, peak_equity=initial_cash)

    async def on_start(self) -> None:
        event = EventEnvelope.create(
            source=self.metadata.plugin_id,
            event_type=PAPER_EXECUTION_RECORDED,
            payload={},
        )
        await self._publish_portfolio(event)

    @subscribes_to(ACCEPTED_CANDIDATE)
    async def handle_accepted_candidate(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        await self._record(event, "signal_accepted", symbol=_text(event.payload.get("symbol"), default="") or None)

    @subscribes_to(REJECTED_CANDIDATE)
    async def handle_rejected_candidate(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        await self._record(event, "signal_rejected", symbol=_text(event.payload.get("symbol"), default="") or None, reason=_text(event.payload.get("reason"), default="") or None)

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        if event.payload.get("source_event_type") != str(TRADE_REQUESTED):
            return
        trade_id = _text(event.payload.get("trade_id"), default="")
        symbol = _text(event.payload.get("symbol"), default="")
        quantity = _number(event.payload.get("quantity"), default=0.0)
        price = _number(event.payload.get("entry_price"), default=0.0)
        if (
            not trade_id
            or not symbol
            or quantity <= 0
            or price <= 0
            or trade_id in self._state.processed_approval_trade_ids
        ):
            return
        self._state.processed_approval_trade_ids.add(trade_id)
        if self._state.trading_halted:
            await self._record(
                event,
                "order_rejected",
                trade_id=trade_id,
                symbol=symbol,
                reason="paper trading is halted by a risk limit",
                risk_limit=True,
            )
            return
        order = VirtualOrder(
            order_id=f"{trade_id}:entry",
            trade_id=trade_id,
            symbol=symbol,
            order_type=_text(event.payload.get("order_type"), default="market"),
            limit_price=_optional_number(event.payload.get("limit_price")),
            stop_price=_optional_number(event.payload.get("stop_price")),
            quantity=quantity,
            submitted_at=event.timestamp,
            eligible_at=event.timestamp + self._latency,
            strategy_id=_text(event.payload.get("strategy_id"), default="") or None,
            side="long",
            sector=_text(event.payload.get("sector"), default="Unclassified"),
            correlation_group=_text(event.payload.get("correlation_group"), default="Unclassified"),
        )
        self._state.orders[order.order_id] = order
        await self._record(
            event,
            "order_submitted",
            trade_id=trade_id,
            order_id=order.order_id,
            symbol=symbol,
            evidence={"quantity": quantity, "price": price, "latency_ms": self._latency.total_seconds() * 1000},
        )
        await self._record(
            event,
            "risk_approved",
            trade_id=trade_id,
            symbol=symbol,
            reason=_text(event.payload.get("reason"), default=""),
        )
        if order.order_type == "market" and self._latency == timedelta(0):
            await self._open_position(order, price, event)
        else:
            await self._publish_portfolio(event)

    @subscribes_to(RISK_REJECTED)
    async def handle_risk_rejected(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        await self._record(event, "risk_rejected", trade_id=_text(event.payload.get("trade_id"), default="") or None, symbol=_text(event.payload.get("symbol"), default="") or None, reason=_text(event.payload.get("reason"), default="") or None)

    @subscribes_to(TRADE_MODIFIED)
    async def handle_trade_modified(self, event: EventEnvelope) -> None:
        if event.source == self.metadata.plugin_id or self._seen_event(event):
            return
        self._count(event.event_type)
        trade_id = _text(event.payload.get("trade_id"), default="")
        position = self._state.positions.get(trade_id)
        changes = _mapping(event.payload.get("changes", {}))
        if position is None:
            return
        current_price = _number(changes.get("current_price"), default=position.last_price)
        if current_price > 0:
            position.last_price = current_price
        partial_exit_quantity = _number(changes.get("partial_exit_quantity"), default=0.0)
        if partial_exit_quantity > 0:
            await self._close_quantity(
                position,
                min(partial_exit_quantity, position.quantity),
                current_price,
                "partial exit simulation",
                event,
            )
            if trade_id not in self._state.positions:
                return
        trailing_distance = _optional_number(changes.get("trailing_distance"))
        trailing_stop_price = _optional_number(changes.get("trailing_stop_price"))
        if trailing_distance is not None or trailing_stop_price is not None:
            self._state.orders[f"{trade_id}:trailing_stop"] = VirtualOrder(
                order_id=f"{trade_id}:trailing_stop",
                trade_id=trade_id,
                symbol=position.symbol,
                order_type="trailing_stop",
                quantity=position.quantity,
                trailing_distance=trailing_distance,
                stop_price=trailing_stop_price,
                highest_price=position.last_price,
                strategy_id=position.strategy_id,
                side=position.side,
            )
        await self._publish_trade_updated(position, "modified", "market", event)
        await self._publish_portfolio(event)

    @subscribes_to(TRADE_CLOSED)
    async def handle_trade_closed(self, event: EventEnvelope) -> None:
        if event.source == self.metadata.plugin_id or self._seen_event(event):
            return
        self._count(event.event_type)
        trade_id = _text(event.payload.get("trade_id"), default="")
        position = self._state.positions.get(trade_id)
        if position is None:
            return
        exit_price = _number(event.payload.get("exit_price"), default=position.last_price)
        reason = _text(event.payload.get("reason"), default="trade closed simulation")
        await self._close_quantity(position, position.quantity, exit_price, reason, event)

    @subscribes_to(STOP_LOSS_UPDATED)
    async def handle_stop_loss_updated(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        await self._set_exit_order(event, order_type="exit_stop", price_key="stop_loss_price")

    @subscribes_to(TAKE_PROFIT_UPDATED)
    async def handle_take_profit_updated(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        await self._set_exit_order(event, order_type="exit_limit", price_key="take_profit_price")

    @subscribes_to(TRAILING_STOP_UPDATED)
    async def handle_trailing_stop_updated(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        await self._set_exit_order(event, order_type="trailing_stop", price_key="trailing_stop_price")

    @subscribes_to(TICK_RECEIVED)
    async def handle_tick_received(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        symbol = _text(event.payload.get("symbol"), default="")
        price = _number(event.payload.get("price"), default=0.0)
        if not symbol or price <= 0:
            return
        await self._mark_symbol(symbol, price, event)
        liquidity = _number(event.payload.get("size"), default=float("inf")) if self._liquidity_constrained else float("inf")
        await self._evaluate_orders(symbol, price, event, liquidity=liquidity)
        await self._enforce_risk_limits(event)
        await self._publish_portfolio(event)

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        if self._seen_event(event):
            return
        self._count(event.event_type)
        symbol = _text(event.payload.get("symbol"), default="")
        close_price = _number(event.payload.get("close_price"), default=0.0)
        if not symbol or close_price <= 0:
            return
        await self._mark_symbol(symbol, close_price, event)
        liquidity = _number(event.payload.get("volume"), default=float("inf")) if self._liquidity_constrained else float("inf")
        await self._evaluate_orders(symbol, close_price, event, liquidity=liquidity, gap_price=_optional_number(event.payload.get("open_price")))
        await self._enforce_risk_limits(event)
        await self._publish_portfolio(event)

    async def publish_paper_trade_updated(self, payload: PaperTradeUpdatedPayload) -> None:
        await self.publish(PAPER_TRADE_UPDATED, payload=payload.to_payload())

    async def publish_paper_trade_closed(self, payload: PaperTradeClosedPayload) -> None:
        await self.publish(PAPER_TRADE_CLOSED, payload=payload.to_payload())

    async def publish_paper_portfolio_updated(self, payload: PaperPortfolioUpdatedPayload) -> None:
        await self.publish(PAPER_PORTFOLIO_UPDATED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if not any(self._event_counts.values()):
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Paper trading engine is running before simulated trading evidence.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Paper trading engine is simulating virtual trading state.",
            details={
                **self._event_counts,
                "virtual_positions": len(self._state.positions),
                "virtual_orders": len(self._state.orders),
                "trade_history": len(self._state.trade_history),
                "equity": self._state.equity,
            },
        )

    def current_state(self) -> PaperTradingState:
        return self._state

    async def _open_position(self, order: VirtualOrder, price: float, event: EventEnvelope) -> None:
        if order.eligible_at is not None and event.timestamp < order.eligible_at:
            self._state.orders[order.order_id] = order
            return
        fill_price, slippage = self._fill_price(price, opening=True)
        rejection = self._risk_limit_reason(order.quantity, fill_price)
        if rejection is not None:
            self._state.orders.pop(order.order_id, None)
            await self._record(event, "fill_rejected", trade_id=order.trade_id, order_id=order.order_id, symbol=order.symbol, reason=rejection, risk_limit=True)
            await self._publish_portfolio(event)
            return
        slippage_cost = slippage * order.quantity
        commission = self._commission(order.quantity, fill_price)
        existing = self._state.positions.get(order.trade_id)
        if existing is None:
            self._state.positions[order.trade_id] = VirtualPosition(
                trade_id=order.trade_id,
                symbol=order.symbol,
                quantity=order.quantity,
                average_price=fill_price,
                last_price=fill_price,
                commissions=commission,
                slippage=slippage_cost,
                realized_pnl=-commission,
                strategy_id=order.strategy_id,
                side=order.side,
                total_quantity=order.quantity,
                sector=order.sector,
                correlation_group=order.correlation_group,
            )
            position = self._state.positions[order.trade_id]
        else:
            if existing.strategy_id != order.strategy_id or existing.side != order.side:
                raise ValueError("An open position cannot combine different strategy identities.")
            if existing.sector != order.sector or existing.correlation_group != order.correlation_group:
                raise ValueError("An open position cannot combine different portfolio classifications.")
            total_quantity = existing.quantity + order.quantity
            existing.average_price = (
                (existing.average_price * existing.quantity) + (fill_price * order.quantity)
            ) / total_quantity
            existing.quantity = total_quantity
            existing.total_quantity += order.quantity
            existing.last_price = fill_price
            existing.commissions += commission
            existing.slippage += slippage_cost
            existing.realized_pnl -= commission
            position = existing
        self._state.cash -= order.quantity * fill_price + commission
        order.status = "filled"
        self._state.orders.pop(order.order_id, None)
        await self._record(event, "fill", trade_id=order.trade_id, order_id=order.order_id, symbol=order.symbol, evidence={"quantity": order.quantity, "price": fill_price, "commission": commission, "slippage": slippage_cost}, execution_id=f"{order.order_id}:fill:{position.total_quantity:g}")
        await self._publish_trade_updated(position, "opened", order.order_type, event)
        self._apply_pending_exit_orders(position)
        if existing is None:
            await self.publish(
                TRADE_OPENED,
                payload=TradeOpenedPayload(
                    trade_id=position.trade_id,
                    symbol=position.symbol,
                    opened_at=event.timestamp,
                    quantity=order.quantity,
                    entry_price=position.average_price,
                    strategy_id=position.strategy_id,
                    side=position.side,
                    source_event_type=str(PAPER_EXECUTION_RECORDED),
                ).to_payload(),
            )
        else:
            await self.publish(
                TRADE_MODIFIED,
                payload=TradeModifiedPayload(
                    trade_id=position.trade_id,
                    symbol=position.symbol,
                    modified_at=event.timestamp,
                    changes={"quantity": position.quantity, "status": "increased"},
                    reason="additional simulated entry fill",
                    source_event_type=str(PAPER_EXECUTION_RECORDED),
                ).to_payload(),
            )
        await self._publish_portfolio(event)

    async def _close_quantity(
        self,
        position: VirtualPosition,
        quantity: float,
        price: float,
        reason: str,
        event: EventEnvelope,
    ) -> None:
        if quantity <= 0 or price <= 0:
            return
        fill_price, slippage = self._fill_price(price, opening=False)
        slippage_cost = slippage * quantity
        commission = self._commission(quantity, fill_price)
        realized_pnl = (fill_price - position.average_price) * quantity - commission
        self._state.cash += quantity * fill_price - commission
        position.quantity -= quantity
        position.closed_quantity += quantity
        position.exit_notional += quantity * fill_price
        position.last_price = fill_price
        position.realized_pnl += realized_pnl
        position.commissions += commission
        position.slippage += slippage_cost
        await self._record(event, "exit_fill", trade_id=position.trade_id, order_id=f"{position.trade_id}:exit", symbol=position.symbol, reason=reason, evidence={"quantity": quantity, "price": fill_price, "realized_pnl": realized_pnl, "commission": commission, "slippage": slippage_cost}, execution_id=f"{position.trade_id}:exit:{position.closed_quantity:g}")
        if position.quantity <= 0:
            self._state.positions.pop(position.trade_id, None)
            average_exit_price = position.exit_notional / position.closed_quantity
            closed_payload = {
                "trade_id": position.trade_id,
                "symbol": position.symbol,
                "quantity": position.closed_quantity,
                "average_price": position.average_price,
                "exit_price": average_exit_price,
                "realized_pnl": position.realized_pnl,
                "commissions": position.commissions,
                "slippage": position.slippage,
                "closed_at": event.timestamp.isoformat(),
                "side": position.side,
            }
            if position.strategy_id is not None:
                closed_payload["strategy_id"] = position.strategy_id
            self._state.trade_history.append(closed_payload)
            await self.publish_paper_trade_closed(
                PaperTradeClosedPayload(
                    trade_id=position.trade_id,
                    symbol=position.symbol,
                    occurred_at=event.timestamp,
                    quantity=position.closed_quantity,
                    average_price=position.average_price,
                    exit_price=average_exit_price,
                    realized_pnl=position.realized_pnl,
                    commissions=position.commissions,
                    slippage=position.slippage,
                    reason=reason,
                    source_event_type=str(event.event_type),
                    strategy_id=position.strategy_id,
                    side=position.side,
                )
            )
            await self.publish(
                TRADE_CLOSED,
                payload=TradeClosedPayload(
                    trade_id=position.trade_id,
                    symbol=position.symbol,
                    closed_at=event.timestamp,
                    reason=reason,
                    exit_price=average_exit_price,
                    source_event_type=str(PAPER_EXECUTION_RECORDED),
                ).to_payload(),
            )
        else:
            await self._publish_trade_updated(position, "partially_closed", "market", event)
            await self.publish(
                TRADE_MODIFIED,
                payload=TradeModifiedPayload(
                    trade_id=position.trade_id,
                    symbol=position.symbol,
                    modified_at=event.timestamp,
                    changes={
                        "quantity": position.quantity,
                        "status": "partially_closed",
                        "realized_pnl": position.realized_pnl,
                    },
                    reason=reason,
                    source_event_type=str(PAPER_EXECUTION_RECORDED),
                ).to_payload(),
            )
        await self._publish_portfolio(event)

    async def _mark_symbol(self, symbol: str, price: float, event: EventEnvelope) -> None:
        for position in self._state.positions.values():
            if position.symbol == symbol:
                position.last_price = price
                await self._publish_trade_updated(position, "marked", "market_data", event)

    async def _evaluate_orders(self, symbol: str, price: float, event: EventEnvelope, *, liquidity: float, gap_price: float | None = None) -> None:
        for order in tuple(self._state.orders.values()):
            if order.symbol != symbol or order.status != "open":
                continue
            if order.eligible_at is not None and event.timestamp < order.eligible_at:
                continue
            fill_quantity = min(order.quantity, max(0.0, liquidity * self._max_order_participation))
            if fill_quantity <= 0:
                continue
            if order.order_type == "market":
                await self._fill_open_order(order, price, fill_quantity, event)
            elif order.order_type == "limit" and order.limit_price is not None and price <= order.limit_price:
                fill_price = min(gap_price, order.limit_price) if gap_price is not None else order.limit_price
                await self._fill_open_order(order, fill_price, fill_quantity, event)
            elif order.order_type == "stop" and order.stop_price is not None and price >= order.stop_price:
                fill_price = max(gap_price, order.stop_price) if gap_price is not None else order.stop_price
                await self._fill_open_order(order, fill_price, fill_quantity, event)
            elif order.order_type == "exit_stop" and order.stop_price is not None and price <= order.stop_price:
                position = self._state.positions.get(order.trade_id)
                if position is not None:
                    fill_quantity = min(fill_quantity, position.quantity, order.quantity)
                    fill_price = min(gap_price, order.stop_price) if gap_price is not None else order.stop_price
                    await self._close_quantity(position, fill_quantity, fill_price, "stop loss simulation", event)
                    order.quantity -= fill_quantity
                    if order.quantity <= 0 or order.trade_id not in self._state.positions:
                        self._state.orders.pop(order.order_id, None)
            elif order.order_type == "exit_limit" and order.limit_price is not None and price >= order.limit_price:
                position = self._state.positions.get(order.trade_id)
                if position is not None:
                    fill_quantity = min(fill_quantity, position.quantity, order.quantity)
                    fill_price = max(gap_price, order.limit_price) if gap_price is not None else order.limit_price
                    await self._close_quantity(position, fill_quantity, fill_price, "take profit simulation", event)
                    order.quantity -= fill_quantity
                    if order.quantity <= 0 or order.trade_id not in self._state.positions:
                        self._state.orders.pop(order.order_id, None)
            elif order.order_type == "trailing_stop":
                await self._evaluate_trailing_stop(
                    order, price, event, liquidity=liquidity, gap_price=gap_price
                )

    async def _evaluate_trailing_stop(
        self,
        order: VirtualOrder,
        price: float,
        event: EventEnvelope,
        *,
        liquidity: float,
        gap_price: float | None,
    ) -> None:
        position = self._state.positions.get(order.trade_id)
        if position is None:
            self._state.orders.pop(order.order_id, None)
            return
        if order.highest_price is None or price > order.highest_price:
            order.highest_price = price
            if order.trailing_distance is not None:
                order.stop_price = price - order.trailing_distance
        if order.stop_price is not None and price <= order.stop_price:
            fill_quantity = min(
                order.quantity,
                position.quantity,
                max(0.0, liquidity * self._max_order_participation),
            )
            if fill_quantity <= 0:
                return
            fill_price = min(gap_price, order.stop_price) if gap_price is not None else order.stop_price
            await self._close_quantity(
                position, fill_quantity, fill_price, "trailing stop simulation", event
            )
            order.quantity -= fill_quantity
            if order.quantity <= 0 or order.trade_id not in self._state.positions:
                self._state.orders.pop(order.order_id, None)

    async def _fill_open_order(self, order: VirtualOrder, price: float, quantity: float, event: EventEnvelope) -> None:
        if quantity >= order.quantity:
            await self._open_position(order, price, event)
            return
        partial = VirtualOrder(
            order_id=order.order_id,
            trade_id=order.trade_id,
            symbol=order.symbol,
            order_type=order.order_type,
            quantity=quantity,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            trailing_distance=order.trailing_distance,
            highest_price=order.highest_price,
            submitted_at=order.submitted_at,
            eligible_at=order.eligible_at,
            strategy_id=order.strategy_id,
            side=order.side,
            sector=order.sector,
            correlation_group=order.correlation_group,
        )
        await self._open_position(partial, price, event)
        order.quantity -= quantity
        self._state.orders[order.order_id] = order
        await self._record(event, "partial_fill", trade_id=order.trade_id, order_id=order.order_id, symbol=order.symbol, evidence={"filled_quantity": quantity, "remaining_quantity": order.quantity})

    def _risk_limit_reason(self, quantity: float, price: float) -> str | None:
        if self._state.trading_halted:
            return "paper trading is halted by a risk limit"
        if len(self._state.positions) >= self._max_positions:
            return "maximum paper position count reached"
        if price > 0 and self._state.equity > 0:
            projected_exposure = (sum(position.market_value for position in self._state.positions.values()) + quantity * price) / self._state.equity
            if projected_exposure > self._max_exposure:
                return "maximum paper exposure exceeded"
        if self._state.cash < quantity * price + self._commission(quantity, price):
            return "insufficient paper cash"
        return None

    async def _enforce_risk_limits(self, event: EventEnvelope) -> None:
        equity = self._state.equity
        self._state.peak_equity = max(self._state.peak_equity, equity)
        prior_days = [
            value
            for occurred_at, value in self._state.equity_history
            if occurred_at.date() < event.timestamp.date()
        ]
        opening_equity = prior_days[-1] if prior_days else self._state.initial_cash
        daily_pnl = equity - opening_equity
        reason: str | None = None
        if self._state.drawdown >= self._max_drawdown:
            reason = "maximum paper drawdown exceeded"
        elif daily_pnl <= -(self._state.initial_cash * self._max_daily_loss):
            reason = "maximum paper daily loss exceeded"
        if reason is None:
            return
        self._state.trading_halted = True
        for position in tuple(self._state.positions.values()):
            await self._close_quantity(position, position.quantity, position.last_price, reason, event)
        await self._record(event, "risk_limit_enforced", reason=reason, risk_limit=True, evidence={"drawdown": self._state.drawdown, "daily_pnl": daily_pnl})

    async def _record(
        self,
        event: EventEnvelope,
        action: str,
        *,
        trade_id: str | None = None,
        order_id: str | None = None,
        symbol: str | None = None,
        reason: str | None = None,
        evidence: dict[str, float | str | bool] | None = None,
        risk_limit: bool = False,
        execution_id: str | None = None,
    ) -> None:
        payload = PaperExecutionRecordedPayload(
            occurred_at=event.timestamp, action=action, trade_id=trade_id, order_id=order_id,
            symbol=symbol, reason=reason, evidence=evidence or {}, execution_id=execution_id,
        ).to_payload()
        self._state.event_history.append({"timestamp": event.timestamp.isoformat(), "event_type": action, "payload": payload, "source_event_type": str(event.event_type)})
        await self.publish(PAPER_RISK_LIMIT_ENFORCED if risk_limit else PAPER_EXECUTION_RECORDED, payload=payload)

    def event_history(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._state.event_history)

    def performance_report(self, period: str, *, as_of: datetime | None = None) -> dict[str, float]:
        timestamp = (as_of or datetime.now(UTC)).astimezone(UTC)
        start = timestamp.date()
        if period == "weekly":
            start = (timestamp - timedelta(days=timestamp.weekday())).date()
        elif period == "monthly":
            start = timestamp.date().replace(day=1)
        elif period != "daily":
            raise ValueError("period must be daily, weekly, or monthly")
        history = sorted(
            (occurred_at.astimezone(UTC), equity)
            for occurred_at, equity in self._state.equity_history
            if occurred_at.tzinfo is not None and occurred_at <= timestamp
        )
        before_period = [equity for occurred_at, equity in history if occurred_at.date() < start]
        period_points = [equity for occurred_at, equity in history if occurred_at.date() >= start]
        opening = before_period[-1] if before_period else self._state.initial_cash
        current_equity = period_points[-1] if period_points else (history[-1][1] if history else opening)
        drawdown_peak = max((self._state.initial_cash, *(equity for _, equity in history)))
        drawdown = 0.0 if drawdown_peak <= 0 else max(0.0, (drawdown_peak - current_equity) / drawdown_peak)
        trade_count = 0
        for trade in self._state.trade_history:
            closed_at_value = trade.get("closed_at")
            if not isinstance(closed_at_value, str):
                continue
            closed_at = datetime.fromisoformat(closed_at_value)
            if closed_at.tzinfo is None:
                raise ValueError("Trade-history closed_at timestamps must be timezone-aware.")
            if start <= closed_at.astimezone(UTC).date() <= timestamp.date() and closed_at.astimezone(UTC) <= timestamp:
                trade_count += 1
        period_pnl = current_equity - opening
        return {
            "equity": current_equity,
            "pnl": period_pnl,
            "return": 0.0 if opening == 0 else period_pnl / opening,
            "drawdown": drawdown,
            "trade_count": float(trade_count),
        }

    async def publish_performance_reports(self, event: EventEnvelope) -> None:
        for period in ("daily", "weekly", "monthly"):
            await self.publish(PAPER_PERFORMANCE_REPORTED, payload=PaperPerformanceReportedPayload(event.timestamp, period, self.performance_report(period, as_of=event.timestamp)).to_payload())

    async def _set_exit_order(self, event: EventEnvelope, *, order_type: str, price_key: str) -> None:
        trade_id = _text(event.payload.get("trade_id"), default="")
        position = self._state.positions.get(trade_id)
        price = _optional_number(event.payload.get(price_key))
        if not trade_id or price is None or price <= 0:
            return
        if position is None:
            self._state.pending_exit_prices.setdefault(trade_id, {})[order_type] = price
            return
        self._add_exit_order(position, order_type, price)
        await self._publish_portfolio(event)

    def _apply_pending_exit_orders(self, position: VirtualPosition) -> None:
        pending = self._state.pending_exit_prices.pop(position.trade_id, {})
        for order_type, price in pending.items():
            self._add_exit_order(position, order_type, price)

    def _add_exit_order(self, position: VirtualPosition, order_type: str, price: float) -> None:
        order = VirtualOrder(
            order_id=f"{position.trade_id}:{order_type}",
            trade_id=position.trade_id,
            symbol=position.symbol,
            order_type=order_type,
            quantity=position.quantity,
            limit_price=price if order_type == "exit_limit" else None,
            stop_price=price if order_type in {"exit_stop", "trailing_stop"} else None,
            highest_price=position.last_price if order_type == "trailing_stop" else None,
            strategy_id=position.strategy_id,
            side=position.side,
        )
        self._state.orders[order.order_id] = order

    async def _publish_trade_updated(
        self,
        position: VirtualPosition,
        status: str,
        order_type: str,
        event: EventEnvelope,
    ) -> None:
        await self.publish_paper_trade_updated(
            PaperTradeUpdatedPayload(
                trade_id=position.trade_id,
                symbol=position.symbol,
                occurred_at=event.timestamp,
                status=status,
                quantity=position.quantity,
                average_price=position.average_price,
                last_price=position.last_price,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=position.unrealized_pnl,
                commissions=position.commissions,
                slippage=position.slippage,
                order_type=order_type,
                source_event_type=str(event.event_type),
                strategy_id=position.strategy_id,
            )
        )

    async def _publish_portfolio(self, event: EventEnvelope) -> None:
        equity = self._state.equity
        self._state.peak_equity = max(self._state.peak_equity, equity)
        self._state.equity_history.append((event.timestamp, equity))
        await self.publish_paper_portfolio_updated(
            PaperPortfolioUpdatedPayload(
                portfolio_id="paper",
                occurred_at=event.timestamp,
                cash=round(self._state.cash, 4),
                equity=round(self._state.equity, 4),
                realized_pnl=round(self._state.realized_pnl, 4),
                unrealized_pnl=round(self._state.unrealized_pnl, 4),
                commissions=round(self._state.commissions, 4),
                slippage=round(self._state.slippage, 4),
                virtual_positions=tuple(position.to_payload() for position in self._state.positions.values()),
                virtual_orders=tuple(order.to_payload() for order in self._state.orders.values()),
                trade_history=tuple(self._state.trade_history),
                source_event_type=str(event.event_type),
                initial_cash=self._state.initial_cash,
                peak_equity=self._state.peak_equity,
            )
        )
        await self.publish_performance_reports(event)

    def _fill_price(self, price: float, *, opening: bool) -> tuple[float, float]:
        adjustment = price * self._slippage_rate
        fill_price = price + adjustment if opening else price - adjustment
        return fill_price, adjustment

    def _commission(self, quantity: float, price: float) -> float:
        return abs(quantity * price) * self._commission_rate

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1

    def _seen_event(self, event: EventEnvelope) -> bool:
        identity = {
            "source": event.source,
            "event_type": str(event.event_type),
            "timestamp": event.timestamp.isoformat(),
            "payload": _plain_mapping(event.payload),
        }
        key = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        if key in self._state.processed_event_keys:
            return True
        self._state.processed_event_keys.add(key)
        return False


def _mapping(value: ImmutableValue | object) -> dict[str, Any]:
    if hasattr(value, "items"):
        return dict(value.items())  # type: ignore[union-attr]
    return {}


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


def _optional_number(value: ImmutableValue | object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _text(value: ImmutableValue | object, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def create_plugin() -> PaperTradingEnginePlugin:
    return PaperTradingEnginePlugin()
