from __future__ import annotations

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.candlestick.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PUBLISHED_EVENTS,
    PatternDetectedPayload,
    PatternInvalidatedPayload,
)
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED


class CandlestickPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="candlestick",
                name="Candlestick",
                version="1.0.0",
            )
        )
        self._candle_events_seen = 0
        self._indicator_events_seen = 0

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Candlestick plugin publishes {event_type}.",
            )

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._candle_events_seen += 1

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._indicator_events_seen += 1

    async def publish_pattern_detected(self, payload: PatternDetectedPayload) -> None:
        await self.publish(PATTERN_DETECTED, payload=payload.to_payload())

    async def publish_pattern_invalidated(self, payload: PatternInvalidatedPayload) -> None:
        await self.publish(PATTERN_INVALIDATED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._candle_events_seen == 0 or self._indicator_events_seen == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Candlestick plugin is running before complete candle and indicator evidence.",
                details={
                    "candle_events_seen": self._candle_events_seen,
                    "indicator_events_seen": self._indicator_events_seen,
                },
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Candlestick plugin is receiving candle and indicator evidence.",
            details={
                "candle_events_seen": self._candle_events_seen,
                "indicator_events_seen": self._indicator_events_seen,
            },
        )


def create_plugin() -> CandlestickPlugin:
    return CandlestickPlugin()
