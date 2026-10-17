from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.platform_events import TIMER_ELAPSED
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.news import (
    MARKET_SENTIMENT_CHANGED,
    NEWS_ANALYZED,
    NEWS_RECEIVED,
    MarketSentimentChangedPayload,
    NewsAnalyzedPayload,
    NewsPlugin,
    NewsReceivedPayload,
)
from tranalyset.plugins.news.contracts import PUBLISHED_EVENTS


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "news"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "news"
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


async def _started_plugin() -> tuple[NewsPlugin, CapturingContext]:
    plugin = NewsPlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_news_plugin_metadata_is_versioned() -> None:
    plugin = NewsPlugin()

    assert plugin.metadata.plugin_id == "news"
    assert plugin.metadata.name == "News"
    assert plugin.metadata.version == "1.0.0"


async def test_news_plugin_installs_published_event_contracts() -> None:
    plugin = NewsPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_news_plugin_subscribes_to_timer_elapsed() -> None:
    plugin = NewsPlugin()

    subscriptions = plugin.subscriptions()

    assert tuple(subscriptions) == (TIMER_ELAPSED,)
    assert len(subscriptions[TIMER_ELAPSED]) == 1


async def test_news_plugin_publishes_required_events() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_news_received(
        NewsReceivedPayload(
            headline="Central bank leaves policy rate unchanged",
            source_name="Example Wire",
            received_at=timestamp,
            url="https://example.test/news/1",
            symbols=("SPY",),
            summary="Policy rate was unchanged.",
        )
    )
    await plugin.publish_news_analyzed(
        NewsAnalyzedPayload(
            headline="Central bank leaves policy rate unchanged",
            analyzed_at=timestamp,
            sentiment="neutral",
            confidence=0.66,
            symbols=("SPY",),
            evidence={"policy_rate_changed": False},
            source_event_type=str(NEWS_RECEIVED),
        )
    )
    await plugin.publish_market_sentiment_changed(
        MarketSentimentChangedPayload(
            market="US equities",
            sentiment="neutral",
            previous_sentiment="risk_on",
            occurred_at=timestamp,
            confidence=0.61,
            evidence_count=3,
            source_event_type=str(NEWS_ANALYZED),
        )
    )

    assert [event.event_type for event in context.published] == [
        NEWS_RECEIVED,
        NEWS_ANALYZED,
        MARKET_SENTIMENT_CHANGED,
    ]
    assert all(event.source == "news" for event in context.published)
    assert context.published[0].payload["headline"] == "Central bank leaves policy rate unchanged"
    assert context.published[2].payload["evidence_count"] == 3


async def test_news_health_reflects_timer_evidence() -> None:
    plugin = NewsPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    await kernel.start()
    initial_health = await kernel.plugin_health("news")
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="scheduler",
            event_type=TIMER_ELAPSED,
            payload={"timer_name": "news-poll", "elapsed_at": _now().isoformat()},
        )
    )
    await kernel.event_bus.wait_until_idle()
    ready_health = await kernel.plugin_health("news")
    await kernel.stop()

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY
    assert ready_health.details["timer_events_seen"] == 1


def test_news_payloads_validate_confidence() -> None:
    payload = NewsAnalyzedPayload(
        headline="Central bank leaves policy rate unchanged",
        analyzed_at=_now(),
        sentiment="neutral",
        confidence=1.5,
    )

    with pytest.raises(ValueError, match="confidence"):
        payload.to_payload()


def test_news_payloads_do_not_include_trading_decisions() -> None:
    payload = MarketSentimentChangedPayload(
        market="US equities",
        sentiment="neutral",
        previous_sentiment=None,
        occurred_at=_now(),
        confidence=0.61,
        evidence_count=3,
    ).to_payload()

    forbidden = {"action", "order", "position_size", "recommendation", "side"}

    assert forbidden.isdisjoint(payload)
