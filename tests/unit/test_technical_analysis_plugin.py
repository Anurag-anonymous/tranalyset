from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.market_data import CANDLE_CLOSED, TICK_RECEIVED
from tranalyset.plugins.technical_analysis import (
    INDICATORS_UPDATED,
    MOMENTUM_CHANGED,
    STRUCTURE_CHANGED,
    TREND_CHANGED,
    IndicatorsUpdatedPayload,
    MomentumChangedPayload,
    StructureChangedPayload,
    TechnicalAnalysisPlugin,
    TrendChangedPayload,
)
from tranalyset.plugins.technical_analysis.contracts import PUBLISHED_EVENTS


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "technical_analysis"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "technical_analysis"
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


async def _started_plugin() -> tuple[TechnicalAnalysisPlugin, CapturingContext]:
    plugin = TechnicalAnalysisPlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_technical_analysis_plugin_metadata_is_versioned() -> None:
    plugin = TechnicalAnalysisPlugin()

    assert plugin.metadata.plugin_id == "technical_analysis"
    assert plugin.metadata.name == "Technical Analysis"
    assert plugin.metadata.version == "1.0.0"


async def test_technical_analysis_plugin_installs_published_event_contracts() -> None:
    plugin = TechnicalAnalysisPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_technical_analysis_plugin_subscribes_to_market_data_events() -> None:
    plugin = TechnicalAnalysisPlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {TICK_RECEIVED, CANDLE_CLOSED}
    assert len(subscriptions[TICK_RECEIVED]) == 1
    assert len(subscriptions[CANDLE_CLOSED]) == 1


async def test_technical_analysis_plugin_publishes_required_events() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_indicators_updated(
        IndicatorsUpdatedPayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            indicators={"rsi": 51.2, "ema_20": 100.25},
            source_event_type=str(CANDLE_CLOSED),
        )
    )
    await plugin.publish_trend_changed(
        TrendChangedPayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            trend="up",
            previous_trend="neutral",
            source_event_type=str(CANDLE_CLOSED),
        )
    )
    await plugin.publish_momentum_changed(
        MomentumChangedPayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            momentum="rising",
            previous_momentum="flat",
            source_event_type=str(TICK_RECEIVED),
        )
    )
    await plugin.publish_structure_changed(
        StructureChangedPayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            structure="higher_high",
            previous_structure="range",
        )
    )

    assert [event.event_type for event in context.published] == [
        INDICATORS_UPDATED,
        TREND_CHANGED,
        MOMENTUM_CHANGED,
        STRUCTURE_CHANGED,
    ]
    assert all(event.source == "technical_analysis" for event in context.published)
    assert context.published[0].payload["indicators"]["rsi"] == 51.2
    assert context.published[1].payload["trend"] == "up"


async def test_technical_analysis_health_reflects_market_data_evidence() -> None:
    plugin = TechnicalAnalysisPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    await kernel.start()
    initial_health = await kernel.plugin_health("technical_analysis")
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="market_data",
            event_type=TICK_RECEIVED,
            payload={
                "symbol": "AAPL",
                "price": 100.25,
                "size": 10.0,
                "received_at": _now().isoformat(),
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    ready_health = await kernel.plugin_health("technical_analysis")
    await kernel.stop()

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY
    assert ready_health.details["market_events_seen"] == 1


def test_technical_analysis_payloads_require_timezone_aware_timestamps() -> None:
    payload = IndicatorsUpdatedPayload(
        symbol="AAPL",
        timeframe="1m",
        occurred_at=datetime(2026, 1, 1, 9, 30),
        indicators={"rsi": 51.2},
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        payload.to_payload()


def test_technical_analysis_payloads_do_not_include_trading_decisions() -> None:
    payload = TrendChangedPayload(
        symbol="AAPL",
        timeframe="1m",
        occurred_at=_now(),
        trend="up",
    ).to_payload()

    forbidden = {"action", "order", "position_size", "recommendation", "side"}

    assert forbidden.isdisjoint(payload)
