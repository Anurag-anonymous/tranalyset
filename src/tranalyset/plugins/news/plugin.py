from __future__ import annotations

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.platform_events import TIMER_ELAPSED
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.news.contracts import (
    MARKET_SENTIMENT_CHANGED,
    NEWS_ANALYZED,
    NEWS_RECEIVED,
    PUBLISHED_EVENTS,
    MarketSentimentChangedPayload,
    NewsAnalyzedPayload,
    NewsReceivedPayload,
)


class NewsPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="news",
                name="News",
                version="1.0.0",
            )
        )
        self._timer_events_seen = 0

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"News plugin publishes {event_type}.",
            )

    @subscribes_to(TIMER_ELAPSED)
    async def handle_timer_elapsed(self, event: EventEnvelope) -> None:
        self._timer_events_seen += 1

    async def publish_news_received(self, payload: NewsReceivedPayload) -> None:
        await self.publish(NEWS_RECEIVED, payload=payload.to_payload())

    async def publish_news_analyzed(self, payload: NewsAnalyzedPayload) -> None:
        await self.publish(NEWS_ANALYZED, payload=payload.to_payload())

    async def publish_market_sentiment_changed(
        self,
        payload: MarketSentimentChangedPayload,
    ) -> None:
        await self.publish(MARKET_SENTIMENT_CHANGED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._timer_events_seen == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="News plugin is running before timer evidence.",
                details={"timer_events_seen": self._timer_events_seen},
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="News plugin is receiving timer evidence.",
            details={"timer_events_seen": self._timer_events_seen},
        )


def create_plugin() -> NewsPlugin:
    return NewsPlugin()
