from __future__ import annotations

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.platform_events import APPLICATION_STARTED
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.market_data.ingestion import MarketDataIngestionService
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.market_data.contracts import (
    CANDLE_CLOSED,
    CONNECTION_LOST,
    CONNECTION_RESTORED,
    MARKET_CLOSED,
    MARKET_OPENED,
    PUBLISHED_EVENTS,
    TICK_RECEIVED,
    CandleClosedPayload,
    ConnectionStatePayload,
    MarketSessionPayload,
    TickReceivedPayload,
)


class MarketDataPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="market_data",
                name="Market Data",
                version="1.0.0",
            )
        )
        self._application_started = False
        self._ingestion: MarketDataIngestionService | None = None

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Market data plugin publishes {event_type}.",
            )

    @subscribes_to(APPLICATION_STARTED)
    async def handle_application_started(self, event: EventEnvelope) -> None:
        self._application_started = True

    async def on_configure(self) -> None:
        if self._context is None:
            raise RuntimeError("Market data plugin is missing its runtime context.")
        service = self._context.resolve(MarketDataIngestionService)
        if not isinstance(service, MarketDataIngestionService):
            raise TypeError("Market data ingestion service is not configured.")
        self._ingestion = service

    async def publish_tick_received(self, payload: TickReceivedPayload) -> None:
        serialized = payload.to_payload()
        await self._require_ingestion().persist_tick_payload(serialized)
        await self.publish(TICK_RECEIVED, payload=serialized)

    async def publish_candle_closed(self, payload: CandleClosedPayload) -> None:
        serialized = payload.to_payload()
        await self._require_ingestion().persist_candle_payload(serialized)
        await self.publish(CANDLE_CLOSED, payload=serialized)

    async def publish_market_opened(self, payload: MarketSessionPayload) -> None:
        await self.publish(MARKET_OPENED, payload=payload.to_payload())

    async def publish_market_closed(self, payload: MarketSessionPayload) -> None:
        await self.publish(MARKET_CLOSED, payload=payload.to_payload())

    async def publish_connection_lost(self, payload: ConnectionStatePayload) -> None:
        await self.publish(CONNECTION_LOST, payload=payload.to_payload())

    async def publish_connection_restored(self, payload: ConnectionStatePayload) -> None:
        await self.publish(CONNECTION_RESTORED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if not self._application_started:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Market data plugin is running before ApplicationStarted.",
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Market data plugin is running.",
        )

    def _require_ingestion(self) -> MarketDataIngestionService:
        if self._ingestion is None:
            raise RuntimeError("Market data ingestion service is not configured.")
        return self._ingestion


def create_plugin() -> MarketDataPlugin:
    return MarketDataPlugin()
