from __future__ import annotations

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED
from tranalyset.plugins.technical_analysis.contracts import (
    INDICATORS_UPDATED,
    MOMENTUM_CHANGED,
    PUBLISHED_EVENTS,
    STRUCTURE_CHANGED,
    TREND_CHANGED,
    IndicatorsUpdatedPayload,
    MomentumChangedPayload,
    StructureChangedPayload,
    TrendChangedPayload,
)


class TechnicalAnalysisPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="technical_analysis",
                name="Technical Analysis",
                version="1.0.0",
            )
        )
        self._market_events_seen = 0

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Technical analysis plugin publishes {event_type}.",
            )

    @subscribes_to(TICK_RECEIVED)
    async def handle_tick_received(self, event: EventEnvelope) -> None:
        self._market_events_seen += 1

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._market_events_seen += 1

    async def publish_indicators_updated(self, payload: IndicatorsUpdatedPayload) -> None:
        await self.publish(INDICATORS_UPDATED, payload=payload.to_payload())

    async def publish_trend_changed(self, payload: TrendChangedPayload) -> None:
        await self.publish(TREND_CHANGED, payload=payload.to_payload())

    async def publish_momentum_changed(self, payload: MomentumChangedPayload) -> None:
        await self.publish(MOMENTUM_CHANGED, payload=payload.to_payload())

    async def publish_structure_changed(self, payload: StructureChangedPayload) -> None:
        await self.publish(STRUCTURE_CHANGED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._market_events_seen == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Technical analysis plugin is running before market data evidence.",
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Technical analysis plugin is receiving market data evidence.",
            details={"market_events_seen": self._market_events_seen},
        )


def create_plugin() -> TechnicalAnalysisPlugin:
    return TechnicalAnalysisPlugin()
