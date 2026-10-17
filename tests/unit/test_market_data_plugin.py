from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.platform_events import APPLICATION_STARTED
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.market_data.ingestion import MarketDataIngestionService
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data import (
    CANDLE_CLOSED,
    CONNECTION_LOST,
    CONNECTION_RESTORED,
    MARKET_CLOSED,
    MARKET_OPENED,
    TICK_RECEIVED,
    CandleClosedPayload,
    ConnectionStatePayload,
    MarketDataPlugin,
    MarketSessionPayload,
    TickReceivedPayload,
)
from tranalyset.plugins.market_data.contracts import PUBLISHED_EVENTS


class CapturingContext:
    def __init__(
        self,
        *,
        config: Mapping[str, object] | None = None,
        services: Mapping[object, object] | None = None,
    ) -> None:
        self.plugin_id = "market_data"
        self.config = config or {}
        self._services = services or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return self._services[key]


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "market_data"
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


async def _started_plugin() -> tuple[MarketDataPlugin, CapturingContext]:
    plugin = MarketDataPlugin()
    repository = MarketDataRepository(InMemoryStorageBackend())
    context = CapturingContext(
        services={MarketDataIngestionService: MarketDataIngestionService(repository)}
    )
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_market_data_plugin_metadata_is_versioned() -> None:
    plugin = MarketDataPlugin()

    assert plugin.metadata.plugin_id == "market_data"
    assert plugin.metadata.name == "Market Data"
    assert plugin.metadata.version == "1.0.0"


async def test_market_data_plugin_installs_published_event_contracts() -> None:
    plugin = MarketDataPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_market_data_plugin_subscribes_to_application_started() -> None:
    plugin = MarketDataPlugin()

    subscriptions = plugin.subscriptions()

    assert tuple(subscriptions) == (APPLICATION_STARTED,)
    assert len(subscriptions[APPLICATION_STARTED]) == 1


async def test_market_data_plugin_publishes_required_events() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_tick_received(
        TickReceivedPayload(
            symbol="AAPL",
            price=100.25,
            size=10.0,
            received_at=timestamp,
            venue="NASDAQ",
        )
    )
    await plugin.publish_candle_closed(
        CandleClosedPayload(
            symbol="AAPL",
            timeframe="1m",
            opened_at=timestamp,
            closed_at=timestamp + timedelta(minutes=1),
            open_price=100.0,
            high_price=101.0,
            low_price=99.5,
            close_price=100.5,
            volume=500.0,
                venue="NASDAQ",
        )
    )
    await plugin.publish_market_opened(MarketSessionPayload(market="US", occurred_at=timestamp))
    await plugin.publish_market_closed(MarketSessionPayload(market="US", occurred_at=timestamp))
    await plugin.publish_connection_lost(
        ConnectionStatePayload(
            connection_name="primary-feed",
            occurred_at=timestamp,
            reason="socket closed",
        )
    )
    await plugin.publish_connection_restored(
        ConnectionStatePayload(connection_name="primary-feed", occurred_at=timestamp)
    )

    assert [event.event_type for event in context.published] == [
        TICK_RECEIVED,
        CANDLE_CLOSED,
        MARKET_OPENED,
        MARKET_CLOSED,
        CONNECTION_LOST,
        CONNECTION_RESTORED,
    ]
    assert all(event.source == "market_data" for event in context.published)
    assert context.published[0].payload["symbol"] == "AAPL"
    assert context.published[4].payload["reason"] == "socket closed"


async def test_market_data_plugin_health_reflects_application_started_event() -> None:
    plugin = MarketDataPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    await kernel.start()
    initial_health = await kernel.plugin_health("market_data")
    await kernel.event_bus.publish(
        EventEnvelope.create(source="application", event_type=APPLICATION_STARTED)
    )
    await kernel.event_bus.wait_until_idle()
    ready_health = await kernel.plugin_health("market_data")
    await kernel.stop()

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY


def test_market_data_payloads_require_timezone_aware_timestamps() -> None:
    payload = TickReceivedPayload(
        symbol="AAPL",
        price=100.25,
        size=10.0,
        received_at=datetime(2026, 1, 1, 9, 30),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        payload.to_payload()
