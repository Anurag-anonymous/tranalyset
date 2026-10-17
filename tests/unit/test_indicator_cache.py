"""Tests for the Indicator Cache Service."""
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.indicators import IndicatorCache, IndicatorKey, IndicatorValue
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, CandleClosedPayload


def _candle_event(symbol="AAPL", exchange="NASDAQ", timeframe=Timeframe.ONE_MINUTE, timestamp=None):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    candle = Candle(
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
    return EventEnvelope.create(
        source="test",
        event_type=CANDLE_CLOSED,
        payload=CandleClosedPayload(symbol=candle.symbol, timeframe=candle.timeframe.value, opened_at=candle.timestamp, closed_at=candle.timestamp, open_price=candle.open, high_price=candle.high, low_price=candle.low, close_price=candle.close, volume=candle.volume, venue=candle.exchange).to_payload(),
    )


class TestIndicatorCache:
    @pytest.fixture
    async def cache(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        cache = IndicatorCache(repository=repo, event_bus=bus)
        yield cache
        await cache.stop()
        await bus.stop()

    async def test_start_subscribes_to_candle_events(self, cache):
        await cache.start()
        assert cache._running is True
        assert cache._subscription is not None

    async def test_stop_unsubscribes(self, cache):
        await cache.start()
        await cache.stop()
        assert cache._running is False

    async def test_put_and_get_indicator(self, cache):
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), 150.0, period=20)
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", period=20)
        assert result is not None
        assert result.value == 150.0
        assert result.key.indicator == "EMA"

    async def test_get_returns_none_when_missing(self, cache):
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "RSI")
        assert result is None

    async def test_get_with_timestamp_returns_closest(self, cache):
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), 100.0, period=10)
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA", datetime(2024, 1, 1, 9, 31, tzinfo=UTC), 101.0, period=10)
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA", period=10, timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC))
        assert result is not None
        assert result.value == 100.0

    async def test_invalidate_specific_timestamp(self, cache):
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "MACD", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), 1.5, period=12)
        await cache.invalidate("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "MACD", period=12, timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC))
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "MACD", period=12)
        assert result is None

    async def test_invalidate_all_periods(self, cache):
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "RSI", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), 70.0, period=14)
        await cache.invalidate("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "RSI")
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "RSI", period=14)
        assert result is None

    async def test_clear_removes_all_entries(self, cache):
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "ATR", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), 2.5, period=14)
        await cache.clear()
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "ATR", period=14)
        assert result is None

    async def test_candle_event_invalidates_indicators(self, cache):
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), 100.0, period=10)
        event = _candle_event()
        await cache._handle_candle(event)
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA", period=10)
        assert result is None

    async def test_compute_if_missing_uses_cache(self, cache):
        calls = []

        async def fake_compute(symbol, exchange, timeframe, timestamp):
            calls.append((symbol, exchange, timeframe, timestamp))
            return 42.0

        result = await cache.compute_if_missing("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", 20, datetime(2024, 1, 1, 9, 30, tzinfo=UTC), fake_compute)
        assert result == 42.0
        assert len(calls) == 1

        # Second call should use cache
        result2 = await cache.compute_if_missing("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", 20, datetime(2024, 1, 1, 9, 30, tzinfo=UTC), fake_compute)
        assert result2 == 42.0
        assert len(calls) == 1  # Not called again

    async def test_supported_indicators(self, cache):
        assert len(cache.supported_indicators) == 10
        assert "BollingerBands" in cache.supported_indicators
        assert "Ichimoku" in cache.supported_indicators

    async def test_put_with_tuple_value(self, cache):
        await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "BollingerBands", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), (100.0, 102.0, 98.0), period=20)
        result = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "BollingerBands", period=20)
        assert result is not None
        assert result.value == (100.0, 102.0, 98.0)
