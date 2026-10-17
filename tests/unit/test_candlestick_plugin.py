from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.candlestick import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    CandlestickPlugin,
    PatternDetectedPayload,
    PatternInvalidatedPayload,
)
from tranalyset.plugins.candlestick.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.market_data import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis import INDICATORS_UPDATED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "candlestick"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "candlestick"
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


async def _started_plugin() -> tuple[CandlestickPlugin, CapturingContext]:
    plugin = CandlestickPlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_candlestick_plugin_metadata_is_versioned() -> None:
    plugin = CandlestickPlugin()

    assert plugin.metadata.plugin_id == "candlestick"
    assert plugin.metadata.name == "Candlestick"
    assert plugin.metadata.version == "1.0.0"


async def test_candlestick_plugin_installs_published_event_contracts() -> None:
    plugin = CandlestickPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_candlestick_plugin_subscribes_to_candle_and_indicator_events() -> None:
    plugin = CandlestickPlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {CANDLE_CLOSED, INDICATORS_UPDATED}
    assert len(subscriptions[CANDLE_CLOSED]) == 1
    assert len(subscriptions[INDICATORS_UPDATED]) == 1


async def test_candlestick_plugin_publishes_required_events() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_pattern_detected(
        PatternDetectedPayload(
            symbol="AAPL",
            timeframe="1m",
            pattern="engulfing",
            occurred_at=timestamp,
            confidence=0.72,
            evidence={"body_ratio": 0.8, "confirmed": True},
            source_event_type=str(CANDLE_CLOSED),
        )
    )
    await plugin.publish_pattern_invalidated(
        PatternInvalidatedPayload(
            symbol="AAPL",
            timeframe="1m",
            pattern="engulfing",
            occurred_at=timestamp,
            reason="confirmation window expired",
            source_event_type=str(INDICATORS_UPDATED),
        )
    )

    assert [event.event_type for event in context.published] == [
        PATTERN_DETECTED,
        PATTERN_INVALIDATED,
    ]
    assert all(event.source == "candlestick" for event in context.published)
    assert context.published[0].payload["confidence"] == 0.72
    assert context.published[1].payload["reason"] == "confirmation window expired"


async def test_candlestick_health_reflects_required_evidence() -> None:
    plugin = CandlestickPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    await kernel.start()
    initial_health = await kernel.plugin_health("candlestick")
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1m",
                "closed_at": _now().isoformat(),
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="technical_analysis",
            event_type=INDICATORS_UPDATED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1m",
                "occurred_at": _now().isoformat(),
                "indicators": {"rsi": 51.2},
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    ready_health = await kernel.plugin_health("candlestick")
    await kernel.stop()

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY
    assert ready_health.details["candle_events_seen"] == 1
    assert ready_health.details["indicator_events_seen"] == 1


def test_candlestick_payloads_validate_confidence() -> None:
    payload = PatternDetectedPayload(
        symbol="AAPL",
        timeframe="1m",
        pattern="engulfing",
        occurred_at=_now(),
        confidence=1.5,
    )

    with pytest.raises(ValueError, match="confidence"):
        payload.to_payload()


def test_candlestick_payloads_do_not_include_trading_decisions() -> None:
    payload = PatternInvalidatedPayload(
        symbol="AAPL",
        timeframe="1m",
        pattern="engulfing",
        occurred_at=_now(),
        reason="confirmation window expired",
    ).to_payload()

    forbidden = {"action", "order", "position_size", "recommendation", "side"}

    assert forbidden.isdisjoint(payload)
