from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.risk import RISK_APPROVED
from tranalyset.plugins.risk import RISK_REJECTED, TRADE_REQUESTED
from tranalyset.plugins.paper_trading_engine import PAPER_EXECUTION_RECORDED, PAPER_TRADE_CLOSED
from tranalyset.plugins.signal_orchestrator.contracts import ACCEPTED_CANDIDATE
from tranalyset.plugins.trade_manager import (
    TRADE_CLOSED,
    TRADE_HEALTH_UPDATED,
    TRADE_MODIFIED,
    TRADE_OPENED,
    TRADE_SIGNAL_GENERATED,
    TRAILING_STOP_UPDATED,
    TradeClosedPayload,
    TradeManagerPlugin,
    TradeModifiedPayload,
    TradeOpenedPayload,
    TrailingStopUpdatedPayload,
)
from tranalyset.plugins.trade_manager.contracts import PUBLISHED_EVENTS


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "trade_manager"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "trade_manager"
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


async def _started_plugin() -> tuple[TradeManagerPlugin, CapturingContext]:
    plugin = TradeManagerPlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_trade_manager_plugin_metadata_is_versioned() -> None:
    plugin = TradeManagerPlugin()

    assert plugin.metadata.plugin_id == "trade_manager"
    assert plugin.metadata.name == "Trade Manager"
    assert plugin.metadata.version == "1.0.0"


async def test_trade_manager_plugin_installs_published_event_contracts() -> None:
    plugin = TradeManagerPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert set(install_context.events) == set(PUBLISHED_EVENTS) | {TRADE_REQUESTED}


def test_trade_manager_plugin_subscribes_to_trade_lifecycle_inputs() -> None:
    plugin = TradeManagerPlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {
        TRADE_SIGNAL_GENERATED,
        ACCEPTED_CANDIDATE,
        RISK_APPROVED,
        RISK_REJECTED,
        PAPER_EXECUTION_RECORDED,
        PAPER_TRADE_CLOSED,
        TRADE_OPENED,
        TRADE_MODIFIED,
        TRADE_CLOSED,
        TRADE_HEALTH_UPDATED,
    }
    assert len(subscriptions[TRADE_SIGNAL_GENERATED]) == 1
    assert len(subscriptions[RISK_APPROVED]) == 1
    assert len(subscriptions[TRADE_HEALTH_UPDATED]) == 1


async def test_trade_manager_plugin_publishes_required_events() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_trade_opened(
        TradeOpenedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            opened_at=timestamp,
            quantity=25.0,
            entry_price=180.0,
            evidence={"risk_approved": True},
            source_event_type=str(RISK_APPROVED),
        )
    )
    await plugin.publish_trade_modified(
        TradeModifiedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            modified_at=timestamp,
            changes={"quantity": 20.0},
            reason="position reduced by lifecycle manager",
            source_event_type=str(TRADE_HEALTH_UPDATED),
        )
    )
    await plugin.publish_trade_closed(
        TradeClosedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            closed_at=timestamp,
            reason="lifecycle completed",
            exit_price=181.5,
            source_event_type=str(TRADE_HEALTH_UPDATED),
        )
    )
    await plugin.publish_trailing_stop_updated(
        TrailingStopUpdatedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            updated_at=timestamp,
            trailing_stop_price=178.25,
            previous_trailing_stop_price=177.5,
            reason="health evidence changed",
            source_event_type=str(TRADE_HEALTH_UPDATED),
        )
    )

    assert [event.event_type for event in context.published] == [
        TRADE_OPENED,
        TRADE_MODIFIED,
        TRADE_CLOSED,
        TRAILING_STOP_UPDATED,
    ]
    assert all(event.source == "trade_manager" for event in context.published)
    assert context.published[0].payload["quantity"] == 25.0
    assert context.published[1].payload["changes"]["quantity"] == 20.0
    assert context.published[3].payload["trailing_stop_price"] == 178.25


async def test_trade_manager_health_reflects_required_evidence() -> None:
    plugin = TradeManagerPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    await kernel.start()
    initial_health = await kernel.plugin_health("trade_manager")
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="strategy",
            event_type=TRADE_SIGNAL_GENERATED,
            payload={"signal_id": "signal-1", "symbol": "AAPL"},
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={"trade_id": "trade-1", "symbol": "AAPL"},
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="trade_monitor",
            event_type=TRADE_HEALTH_UPDATED,
            payload={"trade_id": "trade-1", "status": "stable"},
        )
    )
    await kernel.event_bus.wait_until_idle()
    ready_health = await kernel.plugin_health("trade_manager")
    await kernel.stop()

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY
    assert ready_health.details["signal_events_seen"] == 1
    assert ready_health.details["risk_events_seen"] == 1
    assert ready_health.details["health_events_seen"] == 1


def test_trade_opened_payload_validates_quantity() -> None:
    payload = TradeOpenedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        opened_at=_now(),
        quantity=0,
    )

    with pytest.raises(ValueError, match="quantity"):
        payload.to_payload()


def test_trade_modified_payload_requires_changes() -> None:
    payload = TradeModifiedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        modified_at=_now(),
        changes={},
    )

    with pytest.raises(ValueError, match="changes"):
        payload.to_payload()


def test_trade_manager_payloads_do_not_include_broker_order_instructions() -> None:
    payload = TradeClosedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        closed_at=_now(),
        reason="lifecycle completed",
    ).to_payload()

    forbidden = {"broker_order_id", "order", "recommendation", "side"}

    assert forbidden.isdisjoint(payload)
