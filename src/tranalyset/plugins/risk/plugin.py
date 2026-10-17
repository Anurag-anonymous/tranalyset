from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.domain.risk.models import RiskDecisionOutcome
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.probability_engine.contracts import PROBABILITY_UPDATED
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED
from tranalyset.plugins.risk.contracts import (
    PORTFOLIO_UPDATED,
    POSITION_SIZE_CALCULATED,
    PUBLISHED_EVENTS,
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
    TAKE_PROFIT_UPDATED,
    TRAILING_STOP_UPDATED,
    TRADE_REQUESTED,
    TRADE_UPDATED,
    PositionSizeCalculatedPayload,
    RiskApprovedPayload,
    RiskRejectedPayload,
    StopLossUpdatedPayload,
    TakeProfitUpdatedPayload,
    TrailingStopUpdatedPayload,
)
from tranalyset.plugins.risk.service import RiskEngine, RiskInput

PAPER_TRADE_CLOSED = EventType("PaperTradeClosed")
PAPER_RISK_LIMIT_ENFORCED = EventType("PaperRiskLimitEnforced")


class RiskPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="risk",
                name="Risk",
                version="1.0.0",
            )
        )
        self._trade_events_seen = 0
        self._portfolio_events_seen = 0
        self._probability_events_seen = 0
        self._health_events_seen = 0
        self._context_events_seen = 0
        self._trade_requests: dict[str, dict[str, object]] = {}
        self._probability_updates: dict[str, dict[str, object]] = {}
        self._trade_health_updates: dict[str, dict[str, object]] = {}
        self._market_contexts: dict[tuple[str, str], dict[str, object]] = {}
        self._portfolio_snapshot: dict[str, object] | None = None
        self._evaluated_requests: set[str] = set()
        self._closed_trades: set[str] = set()
        self._paper_halted = False
        self._risk_engine = RiskEngine()

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Risk plugin publishes {event_type}.",
            )

    @subscribes_to(TRADE_REQUESTED)
    async def handle_trade_requested(self, event: EventEnvelope) -> None:
        self._trade_events_seen += 1
        payload = event.payload
        trade_id = self._text(payload.get("trade_id"))
        if not trade_id or trade_id in self._evaluated_requests:
            return
        self._evaluated_requests.add(trade_id)
        self._merge_trade_payload(trade_id, payload)
        if self._paper_halted:
            await self.publish_risk_rejected(
                RiskRejectedPayload(
                    trade_id=trade_id,
                    symbol=self._text(payload.get("symbol")),
                    rejected_at=event.timestamp,
                    reason="Paper trading is halted by a risk limit.",
                    evidence={"paper_trading_halted": True},
                    source_event_type=str(event.event_type),
                )
            )
            return
        await self._evaluate_trade(
            trade_id, source_event_type=str(event.event_type), occurred_at=event.timestamp
        )

    @subscribes_to(TRADE_UPDATED)
    async def handle_trade_updated(self, event: EventEnvelope) -> None:
        self._trade_events_seen += 1
        payload = event.payload
        trade_id = self._text(payload.get("trade_id"))
        if not trade_id:
            return
        self._merge_trade_payload(trade_id, payload)
        await self._evaluate_trade(
            trade_id, source_event_type=str(event.event_type), occurred_at=event.timestamp
        )

    @subscribes_to(PORTFOLIO_UPDATED)
    async def handle_portfolio_updated(self, event: EventEnvelope) -> None:
        self._portfolio_events_seen += 1
        self._portfolio_snapshot = dict(event.payload)
        await self._evaluate_all_trades(occurred_at=event.timestamp)

    @subscribes_to(PROBABILITY_UPDATED)
    async def handle_probability_updated(self, event: EventEnvelope) -> None:
        self._probability_events_seen += 1
        payload = event.payload
        trade_id = self._text(payload.get("trade_id"))
        if not trade_id:
            return
        probability = self._number(payload.get("bullish_probability"), default=0.5)
        confidence_lower = self._number(payload.get("confidence_lower"), default=0.5)
        confidence_upper = self._number(payload.get("confidence_upper"), default=0.5)
        self._probability_updates[trade_id] = {
            "probability_distribution": dict(payload.get("probability_distribution") or {}),
            "probability": probability,
            "confidence": (confidence_lower + confidence_upper) / 2,
        }
        await self._evaluate_trade(trade_id, occurred_at=event.timestamp)

    @subscribes_to(PAPER_TRADE_CLOSED)
    async def handle_paper_trade_closed(self, event: EventEnvelope) -> None:
        trade_id = self._text(event.payload.get("trade_id"))
        if trade_id:
            self._closed_trades.add(trade_id)

    @subscribes_to(PAPER_RISK_LIMIT_ENFORCED)
    async def handle_paper_risk_limit_enforced(self, event: EventEnvelope) -> None:
        self._paper_halted = True

    @subscribes_to(TRADE_HEALTH_UPDATED)
    async def handle_trade_health_updated(self, event: EventEnvelope) -> None:
        self._health_events_seen += 1
        payload = event.payload
        trade_id = self._text(payload.get("trade_id"))
        if not trade_id:
            return
        self._trade_health_updates[trade_id] = {
            "health_score": self._number(payload.get("health_score"), default=50.0),
        }
        await self._evaluate_trade(trade_id, occurred_at=event.timestamp)

    @subscribes_to(MARKET_CONTEXT_UPDATED)
    async def handle_market_context_updated(self, event: EventEnvelope) -> None:
        self._context_events_seen += 1
        payload = event.payload
        symbol = self._text(payload.get("symbol"))
        timeframe = self._text(payload.get("timeframe"))
        if not symbol or not timeframe:
            return
        self._market_contexts[(symbol, timeframe)] = dict(payload)
        for trade_id, trade in self._trade_requests.items():
            if trade.get("symbol") == symbol and trade.get("timeframe") == timeframe:
                await self._evaluate_trade(trade_id, occurred_at=event.timestamp)

    async def publish_risk_approved(self, payload: RiskApprovedPayload) -> None:
        await self.publish(RISK_APPROVED, payload=payload.to_payload())

    async def publish_risk_rejected(self, payload: RiskRejectedPayload) -> None:
        await self.publish(RISK_REJECTED, payload=payload.to_payload())

    async def publish_position_size_calculated(
        self,
        payload: PositionSizeCalculatedPayload,
    ) -> None:
        await self.publish(POSITION_SIZE_CALCULATED, payload=payload.to_payload())

    async def publish_stop_loss_updated(self, payload: StopLossUpdatedPayload) -> None:
        await self.publish(STOP_LOSS_UPDATED, payload=payload.to_payload())

    async def publish_take_profit_updated(self, payload: TakeProfitUpdatedPayload) -> None:
        await self.publish(TAKE_PROFIT_UPDATED, payload=payload.to_payload())

    async def publish_trailing_stop_updated(self, payload: TrailingStopUpdatedPayload) -> None:
        await self.publish(TRAILING_STOP_UPDATED, payload=payload.to_payload())

    async def _evaluate_trade(
        self,
        trade_id: str,
        source_event_type: str = str(TRADE_UPDATED),
        occurred_at: datetime | None = None,
    ) -> None:
        trade = self._trade_requests.get(trade_id)
        if (
            trade is None
            or trade_id in self._closed_trades
            or (trade_id in self._evaluated_requests and source_event_type != str(TRADE_REQUESTED))
        ):
            return
        symbol = self._text(trade.get("symbol"))
        timeframe = self._text(trade.get("timeframe"))
        entry_price = self._number(trade.get("entry_price"))
        if not symbol or not timeframe or entry_price <= 0:
            return

        probability = self._probability_updates.get(trade_id, {})
        health = self._trade_health_updates.get(trade_id, {})
        context = self._market_contexts.get((symbol, timeframe), {})
        portfolio = self._portfolio_snapshot or {}

        supporting = self._dict(trade.get("supporting_evidence"))
        request = source_event_type == str(TRADE_REQUESTED)
        portfolio_capital = self._number(portfolio.get("capital"), default=0.0)
        portfolio_exposure = self._number(portfolio.get("exposure"), default=0.0)
        exposure_ratio = (
            portfolio_exposure / portfolio_capital
            if portfolio_capital > 0
            else (1.0 if portfolio_exposure > 0 else 0.0)
        )
        confidence = self._number(trade.get("confidence"), default=0.5) if request else self._number(probability.get("confidence"), default=0.5)
        risk_input = RiskInput(
            trade_id=trade_id,
            symbol=symbol,
            timeframe=timeframe,
            source_event_type=source_event_type,
            entry_price=entry_price,
            side=self._text(trade.get("side"), default="long"),
            probability=self._number(probability.get("probability"), default=confidence),
            confidence=confidence,
            probability_distribution=self._dict(probability.get("probability_distribution")),
            health_score=self._number(
                health.get("health_score"),
                default=self._number(supporting.get("health_score"), default=100.0 if request else 50.0),
            ),
            portfolio_capital=portfolio_capital,
            portfolio_exposure=exposure_ratio,
            portfolio_drawdown=self._number(portfolio.get("drawdown"), default=0.0),
            portfolio_correlation=self._number(portfolio.get("correlation"), default=0.0),
            sector_correlation=self._number(self._dict(context.get("sector_context")).get("correlation"), default=0.0),
            daily_pnl=self._number(portfolio.get("daily_pnl"), default=0.0),
            weekly_pnl=self._number(portfolio.get("weekly_pnl"), default=0.0),
            monthly_pnl=self._number(portfolio.get("monthly_pnl"), default=0.0),
            liquidity=self._number(self._dict(context.get("liquidity_context")).get("liquidity"), default=0.0),
            gap_risk=self._number(self._dict(context.get("risk_context")).get("gap_risk"), default=0.0),
            tail_risk=self._number(self._dict(context.get("risk_context")).get("tail_risk"), default=0.0),
            expected_drawdown=self._number(self._dict(context.get("risk_context")).get("expected_drawdown"), default=0.02),
            expected_value=self._number(self._dict(context.get("risk_context")).get("expected_value"), default=0.0),
            reward_risk=self._number(
                trade.get("reward_risk"),
                default=self._number(self._dict(context.get("risk_context")).get("reward_risk"), default=1.0),
            ),
        )
        result = self._risk_engine.evaluate(risk_input)
        evaluation_time = occurred_at or datetime.now(tz=UTC)

        quantity = float(result.position_size or 0.0)
        if result.decision.outcome == RiskDecisionOutcome.REJECTED or (request and quantity <= 0):
            await self.publish_risk_rejected(
                RiskRejectedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    rejected_at=evaluation_time,
                    reason=(
                        result.decision.reason
                        if result.decision.outcome == RiskDecisionOutcome.REJECTED
                        else "Risk could not calculate a positive paper position size."
                    ),
                    evidence=result.evidence,
                    source_event_type=source_event_type,
                )
            )
        else:
            await self.publish_risk_approved(
                RiskApprovedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    approved_at=evaluation_time,
                    reason=result.decision.reason,
                    evidence={
                        **result.evidence,
                        "risk_amount": quantity * abs(entry_price - (result.stop_loss_price or entry_price)),
                    },
                    source_event_type=source_event_type,
                    entry_price=entry_price if request else None,
                    quantity=quantity if request else None,
                    stop_loss_price=result.stop_loss_price if request else None,
                    take_profit_price=result.take_profit_price if request else None,
                    trailing_stop_price=result.trailing_stop_price if request else None,
                    strategy_id=self._text(trade.get("strategy_id")) or None,
                    order_type=self._text(trade.get("order_type")) or "market",
                    limit_price=self._number(self._dict(trade.get("supporting_evidence")).get("limit_price"), default=0.0) or None,
                    stop_price=self._number(self._dict(trade.get("supporting_evidence")).get("stop_price"), default=0.0) or None,
                    sector=self._text(trade.get("sector")) or None,
                    correlation_group=self._text(trade.get("correlation_group")) or None,
                )
            )
        if result.position_size is not None and result.position_size > 0:
            await self.publish_position_size_calculated(
                PositionSizeCalculatedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    calculated_at=evaluation_time,
                    quantity=float(result.position_size),
                    notional_value=float(Decimal(str(result.position_size)) * Decimal(str(entry_price))),
                    risk_amount=float(Decimal(str(result.position_size)) * abs(Decimal(str(entry_price)) - Decimal(str(result.stop_loss_price or entry_price)))),
                    source_event_type=source_event_type,
                )
            )
        if result.stop_loss_price is not None:
            await self.publish_stop_loss_updated(
                StopLossUpdatedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    updated_at=evaluation_time,
                    stop_loss_price=result.stop_loss_price,
                    reason="Adaptive stop loss calculated",
                    source_event_type=source_event_type,
                )
            )
        if result.take_profit_price is not None:
            await self.publish_take_profit_updated(
                TakeProfitUpdatedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    updated_at=evaluation_time,
                    take_profit_price=result.take_profit_price,
                    reason="Adaptive take profit calculated",
                    source_event_type=source_event_type,
                )
            )
        if result.trailing_stop_price is not None:
            await self.publish_trailing_stop_updated(
                TrailingStopUpdatedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    updated_at=evaluation_time,
                    trailing_stop_price=result.trailing_stop_price,
                    reason="Adaptive trailing stop calculated",
                    source_event_type=source_event_type,
                )
            )

    async def _evaluate_all_trades(self, *, occurred_at: datetime | None = None) -> None:
        for trade_id in list(self._trade_requests):
            await self._evaluate_trade(trade_id, occurred_at=occurred_at)

    def _build_risk_input(
        self,
        *,
        trade_id: str,
        trade: dict[str, object],
        probability: dict[str, object],
        health: dict[str, object],
        context: dict[str, object],
        portfolio: dict[str, object],
    ) -> RiskInput:
        return RiskInput(
            trade_id=trade_id,
            symbol=self._text(trade.get("symbol")),
            timeframe=self._text(trade.get("timeframe")),
            source_event_type=str(TRADE_UPDATED),
            entry_price=self._number(trade.get("entry_price")),
            side=self._text(trade.get("side"), default="long"),
            probability=self._number(probability.get("probability"), default=0.5),
            confidence=self._number(probability.get("confidence"), default=0.5),
            probability_distribution=self._dict(probability.get("probability_distribution")),
            health_score=self._number(health.get("health_score"), default=50.0),
            portfolio_capital=self._number(portfolio.get("capital"), default=0.0),
            portfolio_exposure=self._number(portfolio.get("exposure"), default=0.0),
            portfolio_drawdown=self._number(portfolio.get("drawdown"), default=0.0),
            portfolio_correlation=self._number(portfolio.get("correlation"), default=0.0),
            sector_correlation=self._number(self._dict(context.get("sector_context")).get("correlation"), default=0.0),
            daily_pnl=self._number(portfolio.get("daily_pnl"), default=0.0),
            weekly_pnl=self._number(portfolio.get("weekly_pnl"), default=0.0),
            monthly_pnl=self._number(portfolio.get("monthly_pnl"), default=0.0),
            liquidity=self._number(self._dict(context.get("liquidity_context")).get("liquidity"), default=0.0),
            gap_risk=self._number(self._dict(context.get("risk_context")).get("gap_risk"), default=0.0),
            tail_risk=self._number(self._dict(context.get("risk_context")).get("tail_risk"), default=0.0),
            expected_drawdown=self._number(self._dict(context.get("risk_context")).get("expected_drawdown"), default=0.02),
            expected_value=self._number(self._dict(context.get("risk_context")).get("expected_value"), default=0.0),
            reward_risk=self._number(self._dict(context.get("risk_context")).get("reward_risk"), default=1.0),
        )

    def _merge_trade_payload(self, trade_id: str, payload: dict[str, object]) -> None:
        current = dict(self._trade_requests.get(trade_id, {}))
        for key, value in payload.items():
            if key != "trade_id" and value is not None:
                current[key] = value
        self._trade_requests[trade_id] = current

    def _text(self, value: object | None, *, default: str = "") -> str:
        if isinstance(value, str) and value:
            return value
        return default

    def _number(self, value: object | None, *, default: float = 0.0) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return default

    def _dict(self, value: object | None) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        return {}

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._trade_events_seen == 0 or self._portfolio_events_seen == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Risk plugin is running before complete trade and portfolio evidence.",
                details={
                    "trade_events_seen": self._trade_events_seen,
                    "portfolio_events_seen": self._portfolio_events_seen,
                },
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Risk plugin is receiving trade and portfolio evidence.",
            details={
                "trade_events_seen": self._trade_events_seen,
                "portfolio_events_seen": self._portfolio_events_seen,
            },
        )


def create_plugin() -> RiskPlugin:
    return RiskPlugin()
