from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.risk import (
    PORTFOLIO_UPDATED,
    POSITION_SIZE_CALCULATED,
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
    TAKE_PROFIT_UPDATED,
    TRADE_REQUESTED,
    TRADE_UPDATED,
    PositionSizeCalculatedPayload,
    RiskApprovedPayload,
    RiskPlugin,
    RiskRejectedPayload,
    StopLossUpdatedPayload,
    TakeProfitUpdatedPayload,
)
from tranalyset.plugins.probability_engine import PROBABILITY_UPDATED
from tranalyset.plugins.trade_health_engine import TRADE_HEALTH_UPDATED
from tranalyset.plugins.context_engine import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.risk.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.risk.plugin import PAPER_RISK_LIMIT_ENFORCED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "risk"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "risk"
        self.events: list[EventType] = []

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        return None

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[object], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        return None


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_plugin() -> tuple[RiskPlugin, CapturingContext]:
    plugin = RiskPlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_risk_plugin_metadata_is_versioned() -> None:
    plugin = RiskPlugin()

    assert plugin.metadata.plugin_id == "risk"
    assert plugin.metadata.name == "Risk"
    assert plugin.metadata.version == "1.0.0"


async def test_risk_plugin_installs_published_event_contracts() -> None:
    plugin = RiskPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_risk_plugin_subscribes_to_trade_and_portfolio_events() -> None:
    plugin = RiskPlugin()

    subscriptions = plugin.subscriptions()

    expected_subscriptions = {
        TRADE_REQUESTED,
        TRADE_UPDATED,
        PORTFOLIO_UPDATED,
        PROBABILITY_UPDATED,
        TRADE_HEALTH_UPDATED,
        MARKET_CONTEXT_UPDATED,
        EventType("PaperTradeClosed"),
        PAPER_RISK_LIMIT_ENFORCED,
    }
    assert set(subscriptions) == expected_subscriptions
    assert len(subscriptions[TRADE_REQUESTED]) == 1
    assert len(subscriptions[TRADE_UPDATED]) == 1
    assert len(subscriptions[PORTFOLIO_UPDATED]) == 1
    assert len(subscriptions[PROBABILITY_UPDATED]) == 1
    assert len(subscriptions[TRADE_HEALTH_UPDATED]) == 1
    assert len(subscriptions[MARKET_CONTEXT_UPDATED]) == 1


async def test_risk_plugin_publishes_required_events() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_risk_approved(
        RiskApprovedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            approved_at=timestamp,
            reason="within configured risk limits",
            evidence={"max_drawdown_ok": True},
            source_event_type=str(TRADE_REQUESTED),
        )
    )
    await plugin.publish_risk_rejected(
        RiskRejectedPayload(
            trade_id="trade-2",
            symbol="MSFT",
            rejected_at=timestamp,
            reason="portfolio exposure limit exceeded",
            evidence={"exposure_after_trade": 1.2},
            source_event_type=str(PORTFOLIO_UPDATED),
        )
    )
    await plugin.publish_position_size_calculated(
        PositionSizeCalculatedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            calculated_at=timestamp,
            quantity=25.0,
            notional_value=4500.0,
            risk_amount=100.0,
            source_event_type=str(TRADE_REQUESTED),
        )
    )
    await plugin.publish_stop_loss_updated(
        StopLossUpdatedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            updated_at=timestamp,
            stop_loss_price=171.25,
            previous_stop_loss_price=170.0,
            reason="risk boundary recalculated",
            source_event_type=str(TRADE_UPDATED),
        )
    )
    await plugin.publish_take_profit_updated(
        TakeProfitUpdatedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            updated_at=timestamp,
            take_profit_price=185.0,
            previous_take_profit_price=180.0,
            reason="profit target updated",
            source_event_type=str(TRADE_UPDATED),
        )
    )

    assert [event.event_type for event in context.published] == [
        RISK_APPROVED,
        RISK_REJECTED,
        POSITION_SIZE_CALCULATED,
        STOP_LOSS_UPDATED,
        TAKE_PROFIT_UPDATED,
    ]
    assert all(event.source == "risk" for event in context.published)
    assert context.published[0].payload["reason"] == "within configured risk limits"
    assert context.published[2].payload["quantity"] == 25.0
    assert context.published[3].payload["stop_loss_price"] == 171.25


async def test_risk_outputs_keep_the_triggering_historical_timestamp() -> None:
    plugin, context = await _started_plugin()
    historical_time = datetime(2020, 1, 2, 15, 45, tzinfo=UTC)

    await plugin.handle_trade_requested(
        EventEnvelope(
            timestamp=historical_time,
            source="replay",
            event_type=TRADE_REQUESTED,
            payload={
                "trade_id": "historical-trade",
                "symbol": "AAPL",
                "timeframe": "1m",
                "entry_price": 100.0,
                "side": "long",
            },
        )
    )

    decision = context.published[0]
    assert decision.payload["occurred_at"] == historical_time.isoformat()
    assert decision.timestamp == historical_time


async def test_risk_normalizes_portfolio_exposure_to_fraction_of_capital() -> None:
    plugin, context = await _started_plugin()
    await plugin.handle_portfolio_updated(
        EventEnvelope.create(
            source="portfolio_engine",
            event_type=PORTFOLIO_UPDATED,
            timestamp=_now(),
            payload={"capital": 100_000.0, "exposure": 10_000.0},
        )
    )

    await plugin.handle_trade_requested(
        EventEnvelope.create(
            source="trade_manager",
            event_type=TRADE_REQUESTED,
            timestamp=_now(),
            payload={
                "trade_id": "exposure-test",
                "signal_id": "signal-exposure-test",
                "strategy_id": "strategy",
                "symbol": "AAPL",
                "timeframe": "1m",
                "entry_price": 100.0,
                "confidence": 0.9,
                "reward_risk": 2.0,
                "side": "long",
            },
        )
    )

    approval = next(event for event in context.published if event.event_type == RISK_APPROVED)
    assert approval.payload["evidence"]["portfolio_exposure"] == pytest.approx(0.1)


async def test_risk_rejects_requests_after_paper_risk_limit_halts_trading() -> None:
    plugin, context = await _started_plugin()
    await plugin.handle_paper_risk_limit_enforced(
        EventEnvelope.create(
            source="paper_trading_engine",
            event_type=PAPER_RISK_LIMIT_ENFORCED,
            timestamp=_now(),
            payload={"reason": "maximum paper drawdown exceeded"},
        )
    )

    await plugin.handle_trade_requested(
        EventEnvelope.create(
            source="trade_manager",
            event_type=TRADE_REQUESTED,
            timestamp=_now(),
            payload={
                "trade_id": "halted-request",
                "symbol": "AAPL",
                "timeframe": "1m",
                "entry_price": 100.0,
                "confidence": 0.9,
                "reward_risk": 2.0,
                "side": "long",
            },
        )
    )

    rejection = next(event for event in context.published if event.event_type == RISK_REJECTED)
    assert rejection.payload["reason"] == "Paper trading is halted by a risk limit."
    assert rejection.payload["evidence"]["paper_trading_halted"] is True


async def test_risk_health_reflects_trade_and_portfolio_evidence() -> None:
    plugin = RiskPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    await kernel.start()
    initial_health = await kernel.plugin_health("risk")
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="execution",
            event_type=TRADE_REQUESTED,
            payload={"trade_id": "trade-1", "symbol": "AAPL"},
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="portfolio",
            event_type=PORTFOLIO_UPDATED,
            payload={"portfolio_id": "primary", "updated_at": _now().isoformat()},
        )
    )
    await kernel.event_bus.wait_until_idle()
    ready_health = await kernel.plugin_health("risk")
    await kernel.stop()

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY
    assert ready_health.details["trade_events_seen"] == 1
    assert ready_health.details["portfolio_events_seen"] == 1


def test_position_size_payload_validates_quantity() -> None:
    payload = PositionSizeCalculatedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        calculated_at=_now(),
        quantity=0,
    )

    with pytest.raises(ValueError, match="quantity"):
        payload.to_payload()


def test_stop_loss_payload_validates_price() -> None:
    payload = StopLossUpdatedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        updated_at=_now(),
        stop_loss_price=0,
    )

    with pytest.raises(ValueError, match="stop_loss_price"):
        payload.to_payload()


def test_risk_payloads_do_not_include_order_instructions() -> None:
    payload = RiskApprovedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        approved_at=_now(),
        reason="within configured risk limits",
    ).to_payload()

    forbidden = {"action", "order", "recommendation", "side"}

    assert forbidden.isdisjoint(payload)
