from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.calendar import MarketCalendarService
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import MarketCalendar, MarketSession, Timeframe, TradingSession
from tranalyset.market_data.replay import PlaybackMode, ReplayEngine
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data.contracts import (
    CANDLE_CLOSED,
    MARKET_CLOSED,
    MARKET_OPENED,
    TICK_RECEIVED,
    CandleClosedPayload,
    MarketSessionPayload,
    TickReceivedPayload,
)


async def test_canonical_tick_reaches_timeframe_aggregation() -> None:
    bus = AsyncEventBus()
    repository = MarketDataRepository(InMemoryStorageBackend())
    aggregator = TimeframeAggregationEngine(repository, bus, supported_timeframes=[Timeframe.ONE_MINUTE])
    await bus.start()
    await aggregator.start()
    timestamp = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    await bus.publish(EventEnvelope.create(
        source="market_data",
        event_type=TICK_RECEIVED,
        timestamp=timestamp,
        payload=TickReceivedPayload("AAPL", 100.0, 10.0, timestamp, venue="NASDAQ", side="buy").to_payload(),
    ))
    await bus.wait_until_idle()

    assert len(aggregator._states) == 1
    state = next(iter(aggregator._states.values()))
    assert state.current_builder is not None
    assert state.current_builder.close == 100.0
    await aggregator.stop()
    await bus.stop()


async def test_canonical_candle_reaches_aggregation_and_indicator_cache() -> None:
    bus = AsyncEventBus()
    repository = MarketDataRepository(InMemoryStorageBackend())
    aggregator = TimeframeAggregationEngine(repository, bus, supported_timeframes=[Timeframe.FIVE_MINUTES])
    cache = IndicatorCache(repository, bus)
    await bus.start()
    await aggregator.start()
    await cache.start()
    timestamp = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA", timestamp, 100.0)
    await bus.publish(EventEnvelope.create(
        source="market_data",
        event_type=CANDLE_CLOSED,
        timestamp=timestamp + timedelta(minutes=1),
        payload=CandleClosedPayload("AAPL", "1m", timestamp, timestamp + timedelta(minutes=1), 100.0, 102.0, 99.0, 101.0, 100.0, venue="NASDAQ").to_payload(),
    ))
    await bus.wait_until_idle()

    assert len(aggregator._states) == 1
    assert await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA") is None
    await cache.stop()
    await aggregator.stop()
    await bus.stop()


async def test_replay_uses_the_same_canonical_tick_contract_as_live_data() -> None:
    bus = AsyncEventBus()
    repository = MarketDataRepository(InMemoryStorageBackend())
    aggregator = TimeframeAggregationEngine(repository, bus, supported_timeframes=[Timeframe.ONE_MINUTE])
    replay = ReplayEngine(repository, bus)
    timestamp = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    from tranalyset.market_data.models import Tick
    await repository.append_tick(Tick("AAPL", "NASDAQ", timestamp, 100.0, 1.0))
    received: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        received.append(event)

    await bus.start()
    await aggregator.start()
    await bus.subscribe(TICK_RECEIVED, collect)
    await replay.load_session("AAPL", "NASDAQ", timestamp, timestamp + timedelta(minutes=1))
    replay.playback_mode = PlaybackMode.STEP
    await replay.step()
    await bus.wait_until_idle()

    assert received[0].event_type == TICK_RECEIVED
    assert received[0].payload["venue"] == "NASDAQ"
    assert len(aggregator._states) == 1
    await aggregator.stop()
    await bus.stop()


async def test_canonical_market_session_events_reach_calendar_and_consumers() -> None:
    bus = AsyncEventBus()
    repository = MarketDataRepository(InMemoryStorageBackend())
    calendar = MarketCalendar(
        exchange="NASDAQ", timezone="UTC",
        sessions=(TradingSession("NASDAQ", MarketSession.REGULAR, datetime(2024, 1, 2, 9, 30, tzinfo=UTC), datetime(2024, 1, 2, 16, 0, tzinfo=UTC)),),
    )
    service = MarketCalendarService(repository, bus)
    await bus.start()
    await service.register_calendar(calendar)
    await service.start()
    seen: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        seen.append(event)

    await bus.subscribe(MARKET_OPENED, collect)
    await bus.subscribe(MARKET_CLOSED, collect)
    timestamp = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    await bus.publish(EventEnvelope.create(source="market_data", event_type=MARKET_OPENED, timestamp=timestamp, payload=MarketSessionPayload("NASDAQ", timestamp, venue="NASDAQ").to_payload()))
    await bus.publish(EventEnvelope.create(source="market_data", event_type=MARKET_CLOSED, timestamp=timestamp + timedelta(hours=7), payload=MarketSessionPayload("NASDAQ", timestamp + timedelta(hours=7), venue="NASDAQ").to_payload()))
    await bus.wait_until_idle()

    assert [event.event_type for event in seen] == [MARKET_OPENED, MARKET_CLOSED]
    assert service._current_sessions["NASDAQ"] is None
    await service.stop()
    await bus.stop()


async def test_invalid_canonical_payloads_fail_explicitly() -> None:
    bus = AsyncEventBus()
    aggregator = TimeframeAggregationEngine(MarketDataRepository(InMemoryStorageBackend()), bus)
    await bus.start()
    with pytest.raises(ValueError, match="venue"):
        await aggregator._handle_tick(EventEnvelope.create(
            source="market_data", event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "price": 100.0, "size": 1.0, "received_at": datetime(2024, 1, 2, tzinfo=UTC).isoformat()},
        ))
    await bus.stop()
