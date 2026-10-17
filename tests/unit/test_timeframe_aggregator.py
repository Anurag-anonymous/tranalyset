"""Tests for the Timeframe Aggregation Engine."""
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.models import Candle, Tick, Timeframe
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data.contracts import (
    CANDLE_CLOSED,
    TICK_RECEIVED,
    TIMEFRAME_CANDLE_CLOSED,
    CandleClosedPayload,
    TickReceivedPayload,
)


def _tick(symbol="AAPL", exchange="NASDAQ", timestamp=None, price=100.0, size=1000.0):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Tick(symbol=symbol, exchange=exchange, timestamp=ts, price=price, size=size)


def _candle(symbol="AAPL", exchange="NASDAQ", timeframe=Timeframe.ONE_MINUTE, timestamp=None):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Candle(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        timestamp=ts,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1000.0,
        trades=42,
    )


def _tick_event(symbol="AAPL", exchange="NASDAQ", timestamp=None, price=100.0, size=1000.0):
    tick = _tick(symbol=symbol, exchange=exchange, timestamp=timestamp, price=price, size=size)
    return EventEnvelope.create(
        source="test",
        event_type=TICK_RECEIVED,
        payload=TickReceivedPayload(symbol=tick.symbol, price=tick.price, size=tick.size, received_at=tick.timestamp, venue=tick.exchange, side=tick.side).to_payload(),
        timestamp=tick.timestamp,
    )


def _candle_event(symbol="AAPL", exchange="NASDAQ", timeframe=Timeframe.ONE_MINUTE, timestamp=None):
    candle = _candle(symbol=symbol, exchange=exchange, timeframe=timeframe, timestamp=timestamp)
    return EventEnvelope.create(
        source="test",
        event_type=CANDLE_CLOSED,
        payload=CandleClosedPayload(symbol=candle.symbol, timeframe=candle.timeframe.value, opened_at=candle.timestamp, closed_at=candle.timestamp, open_price=candle.open, high_price=candle.high, low_price=candle.low, close_price=candle.close, volume=candle.volume, venue=candle.exchange).to_payload(),
        timestamp=candle.timestamp,
    )


class TestTimeframeAggregationEngine:
    @pytest.fixture
    async def engine(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        engine = TimeframeAggregationEngine(
            repository=repo,
            event_bus=bus,
            supported_timeframes=[Timeframe.ONE_MINUTE, Timeframe.FIVE_MINUTES, Timeframe.ONE_HOUR],
        )
        yield engine
        await engine.stop()
        await bus.stop()

    async def test_start_subscribes_to_events(self, engine):
        await engine.start()
        assert engine._running is True
        assert engine._subscription_tick is not None
        assert engine._subscription_candle is not None

    async def test_stop_unsubscribes(self, engine):
        await engine.start()
        await engine.stop()
        assert engine._running is False

    async def test_single_tick_creates_partial_candle(self, engine):
        await engine.start()
        event = _tick_event()
        await engine._handle_tick(event)

        assert len(engine._states) == 3  # 1m, 5m, 1h
        key_1m = next(k for k in engine._states.keys() if k.timeframe == Timeframe.ONE_MINUTE)
        state = engine._states[key_1m]
        assert state.current_builder is not None
        assert state.current_builder.tick_count == 1
        assert state.current_builder.open == 100.0

    async def test_multiple_ticks_aggregate_into_candle(self, engine):
        await engine.start()
        for i in range(3):
            event = _tick_event(timestamp=datetime(2024, 1, 1, 9, 30, 15 + i, tzinfo=UTC))
            await engine._handle_tick(event)

        # No candle published yet - need to cross minute boundary
        assert engine._states[next(k for k in engine._states.keys() if k.timeframe == Timeframe.ONE_MINUTE)].current_builder is not None

    async def test_process_candle_from_lower_timeframe(self, engine):
        await engine.start()
        candle = _candle(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC))
        event = _candle_event(timestamp=candle.timestamp)
        await engine._handle_candle(event)

        key_5m = next(k for k in engine._states.keys() if k.timeframe == Timeframe.FIVE_MINUTES)
        state = engine._states[key_5m]
        assert state.current_builder is not None
        assert state.current_builder.close == 101.0

    async def test_higher_timeframe_uses_its_own_boundary_and_identity(self):
        backend = InMemoryStorageBackend()
        bus = AsyncEventBus()
        await bus.start()
        engine = TimeframeAggregationEngine(
            repository=MarketDataRepository(backend),
            event_bus=bus,
            supported_timeframes=[Timeframe.FIVE_MINUTES],
        )
        await engine.start()
        published: list[EventEnvelope] = []

        async def collect(event: EventEnvelope) -> None:
            published.append(event)

        await bus.subscribe(TIMEFRAME_CANDLE_CLOSED, collect)
        await engine._handle_tick(_tick_event(timestamp=datetime(2024, 1, 1, 9, 31, tzinfo=UTC), price=100.0))
        await engine._handle_tick(_tick_event(timestamp=datetime(2024, 1, 1, 9, 34, tzinfo=UTC), price=102.0))
        await engine._handle_tick(_tick_event(timestamp=datetime(2024, 1, 1, 9, 35, tzinfo=UTC), price=101.0))
        await bus.wait_until_idle()

        assert published[0].payload["symbol"] == "AAPL"
        assert published[0].payload["venue"] == "NASDAQ"
        assert published[0].payload["timeframe"] == Timeframe.FIVE_MINUTES.value
        assert published[0].payload["opened_at"] == datetime(2024, 1, 1, 9, 30, tzinfo=UTC).isoformat()
        assert published[0].payload["close_price"] == 102.0
        await engine.stop()
        await bus.stop()

    async def test_tick_event_contract_drives_aggregation_through_the_bus(self):
        backend = InMemoryStorageBackend()
        bus = AsyncEventBus()
        await bus.start()
        engine = TimeframeAggregationEngine(
            repository=MarketDataRepository(backend),
            event_bus=bus,
            supported_timeframes=[Timeframe.ONE_MINUTE],
        )
        await engine.start()
        await bus.publish(_tick_event(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC)))
        await bus.wait_until_idle()

        assert len(engine._states) == 1
        await engine.stop()
        await bus.stop()

    async def test_aggregation_finalizes_on_period_boundary(self, engine):
        await engine.start()

        # Fill current minute with ticks
        for i in range(3):
            event = _tick_event(timestamp=datetime(2024, 1, 1, 9, 30, 15 + i, tzinfo=UTC))
            await engine._handle_tick(event)

        # One more tick in next minute triggers finalization
        event = _tick_event(timestamp=datetime(2024, 1, 1, 9, 31, 0, tzinfo=UTC))
        await engine._handle_tick(event)

        # After finalization, a new builder should exist for 9:31
        key_1m = next(k for k in engine._states.keys() if k.timeframe == Timeframe.ONE_MINUTE)
        state = engine._states[key_1m]
        assert state.current_builder is not None
        assert state.current_builder.timestamp == datetime(2024, 1, 1, 9, 31, 0, tzinfo=UTC)

    async def test_supported_timeframes_property(self, engine):
        assert engine.supported_timeframes == [Timeframe.ONE_MINUTE, Timeframe.FIVE_MINUTES, Timeframe.ONE_HOUR]

    async def test_custom_timeframes(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        engine = TimeframeAggregationEngine(
            repository=repo,
            event_bus=bus,
            supported_timeframes=[Timeframe.TEN_MINUTES, Timeframe.THIRTY_MINUTES],
        )
        assert engine.supported_timeframes == [Timeframe.TEN_MINUTES, Timeframe.THIRTY_MINUTES]
        await engine.stop()
        await bus.stop()


class TestAggregationLogic:
    async def test_tick_aggregation_ohlc(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        engine = TimeframeAggregationEngine(
            repository=repo,
            event_bus=bus,
            supported_timeframes=[Timeframe.ONE_MINUTE],
        )
        await engine.start()

        prices = [100.0, 102.0, 98.0, 101.0]
        timestamps = [
            datetime(2024, 1, 1, 9, 30, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 9, 30, 15, tzinfo=UTC),
            datetime(2024, 1, 1, 9, 30, 30, tzinfo=UTC),
            datetime(2024, 1, 1, 9, 30, 45, tzinfo=UTC),
        ]

        for price, ts in zip(prices, timestamps):
            event = _tick_event(timestamp=ts, price=price)
            await engine._handle_tick(event)

        # Verify aggregation state
        key = next(k for k in engine._states.keys() if k.timeframe == Timeframe.ONE_MINUTE)
        state = engine._states[key]
        assert state.current_builder is not None
        assert state.current_builder.open == 100.0
        assert state.current_builder.high == 102.0
        assert state.current_builder.low == 98.0
        assert state.current_builder.close == 101.0
        assert state.current_builder.volume == 4000.0
        assert state.current_builder.trades == 4

        await engine.stop()
        await bus.stop()

    async def test_candle_aggregation_skips_unsupported(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        engine = TimeframeAggregationEngine(
            repository=repo,
            event_bus=bus,
            supported_timeframes=[Timeframe.FIVE_MINUTES],
        )
        await engine.start()

        # 1m candle should not be aggregated into 5m if target is not divisible
        candle = _candle(timeframe=Timeframe.ONE_MINUTE, timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC))
        event = _candle_event(timeframe=Timeframe.ONE_MINUTE, timestamp=candle.timestamp)
        await engine._handle_candle(event)

        # 5m state exists but no aggregation yet
        key = next(k for k in engine._states.keys() if k.timeframe == Timeframe.FIVE_MINUTES)
        state = engine._states[key]
        assert state.current_builder is not None

        await engine.stop()
        await bus.stop()

    async def test_missing_ticks_handled_with_partial_candle(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        engine = TimeframeAggregationEngine(
            repository=repo,
            event_bus=bus,
            supported_timeframes=[Timeframe.ONE_MINUTE],
        )
        await engine.start()

        # Only 2 ticks in a minute - partial candle
        event1 = _tick_event(timestamp=datetime(2024, 1, 1, 9, 30, 0, tzinfo=UTC), price=100.0)
        event2 = _tick_event(timestamp=datetime(2024, 1, 1, 9, 30, 30, tzinfo=UTC), price=102.0)
        await engine._handle_tick(event1)
        await engine._handle_tick(event2)

        key = next(k for k in engine._states.keys() if k.timeframe == Timeframe.ONE_MINUTE)
        state = engine._states[key]
        assert state.current_builder is not None
        assert state.current_builder.tick_count == 2

        await engine.stop()
        await bus.stop()
