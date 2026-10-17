from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.market_data.ingestion import MarketDataIngestionService
from tranalyset.market_data.models import Tick, Timeframe
from tranalyset.market_data.replay import PlaybackMode, ReplayEngine
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data import (
    TICK_RECEIVED,
    CandleClosedPayload,
    MarketDataPlugin,
    TickReceivedPayload,
)


async def _running_market_data_plugin() -> tuple[Microkernel, MarketDataPlugin, MarketDataRepository]:
    plugin = MarketDataPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))
    await kernel.start()
    repository = kernel.container.resolve(MarketDataRepository)
    assert isinstance(repository, MarketDataRepository)
    return kernel, plugin, repository


async def test_live_tick_is_persisted_before_downstream_delivery() -> None:
    kernel, plugin, repository = await _running_market_data_plugin()
    timestamp = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    observed: list[bool] = []

    async def repository_is_current(event: EventEnvelope) -> None:
        ticks = await repository.get_ticks("AAPL", "NASDAQ")
        observed.append(len(ticks) == 1)

    await kernel.event_bus.subscribe(TICK_RECEIVED, repository_is_current)
    await plugin.publish_tick_received(
        TickReceivedPayload("AAPL", 100.0, 10.0, timestamp, venue="NASDAQ", side="buy")
    )
    await kernel.event_bus.wait_until_idle()

    ticks = await repository.get_ticks("AAPL", "NASDAQ")
    assert ticks == (Tick("AAPL", "NASDAQ", timestamp, 100.0, 10.0, "buy"),)
    assert observed == [True]
    await kernel.stop()


async def test_live_source_candle_is_persisted() -> None:
    kernel, plugin, repository = await _running_market_data_plugin()
    opened_at = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    await plugin.publish_candle_closed(
        CandleClosedPayload(
            "AAPL", "1m", opened_at, opened_at + timedelta(minutes=1),
            100.0, 102.0, 99.0, 101.0, 500.0, venue="NASDAQ",
        )
    )
    await kernel.event_bus.wait_until_idle()

    candles = await repository.get_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE)
    assert len(candles) == 1
    assert candles[0].timestamp == opened_at
    assert candles[0].close == 101.0
    await kernel.stop()


async def test_finalized_aggregated_candle_is_persisted_before_publication() -> None:
    kernel, plugin, repository = await _running_market_data_plugin()
    start = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    await plugin.publish_tick_received(TickReceivedPayload("AAPL", 100.0, 1.0, start, venue="NASDAQ"))
    await plugin.publish_tick_received(
        TickReceivedPayload("AAPL", 101.0, 1.0, start + timedelta(minutes=1), venue="NASDAQ")
    )
    await kernel.event_bus.wait_until_idle()

    candles = await repository.get_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE)
    assert len(candles) == 1
    assert candles[0].timestamp == start
    assert candles[0].open == 100.0
    assert candles[0].close == 100.0
    await kernel.stop()


async def test_retry_does_not_duplicate_persisted_market_data() -> None:
    kernel, plugin, repository = await _running_market_data_plugin()
    timestamp = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    payload = TickReceivedPayload("AAPL", 100.0, 10.0, timestamp, venue="NASDAQ")
    await plugin.publish_tick_received(payload)
    await plugin.publish_tick_received(payload)
    await kernel.event_bus.wait_until_idle()

    assert len(await repository.get_ticks("AAPL", "NASDAQ")) == 1
    await kernel.stop()


async def test_persistence_failure_prevents_live_publication() -> None:
    class FailingBackend(InMemoryStorageBackend):
        async def append_tick(self, tick: Tick) -> None:
            raise RuntimeError("storage unavailable")

    class Context:
        plugin_id = "market_data"
        config: Mapping[str, object] = {}

        def __init__(self) -> None:
            self.published: list[EventEnvelope] = []
            self._ingestion = MarketDataIngestionService(MarketDataRepository(FailingBackend()))

        async def publish(self, event: EventEnvelope) -> None:
            self.published.append(event)

        def resolve(self, key: object) -> object:
            assert key is MarketDataIngestionService
            return self._ingestion

    class InstallContext:
        plugin_id = "market_data"

        def register_event(
            self,
            event_type: object,
            *,
            description: str,
            replace: bool = False,
        ) -> None:
            return None

        def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
            return None

        def register_service_factory(
            self,
            key: object,
            factory: object,
            *,
            singleton: bool = True,
            replace: bool = False,
        ) -> None:
            return None

    plugin = MarketDataPlugin()
    context = Context()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await plugin.publish_tick_received(
            TickReceivedPayload("AAPL", 100.0, 1.0, datetime(2024, 1, 2, tzinfo=UTC), venue="NASDAQ")
        )
    assert context.published == []


async def test_replay_uses_the_same_idempotent_repository_semantics_as_live_ingestion() -> None:
    repository = MarketDataRepository(InMemoryStorageBackend())
    tick = Tick("AAPL", "NASDAQ", datetime(2024, 1, 2, 9, 30, tzinfo=UTC), 100.0, 1.0)
    assert await repository.append_tick(tick) is True

    from tranalyset.kernel.event_bus import AsyncEventBus
    bus = AsyncEventBus()
    replay = ReplayEngine(repository, bus)
    await bus.start()
    await replay.load_session("AAPL", "NASDAQ", tick.timestamp, tick.timestamp + timedelta(minutes=1))
    replay.playback_mode = PlaybackMode.STEP
    await replay.step()

    assert len(await repository.get_ticks("AAPL", "NASDAQ")) == 1
    await bus.stop()
