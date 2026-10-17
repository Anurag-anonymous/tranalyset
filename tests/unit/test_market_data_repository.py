"""Tests for the Market Data Repository bounded context."""
from datetime import UTC, datetime

import pytest

from tranalyset.market_data.models import (
    Candle,
    InstrumentMetadata,
    MarketCalendar,
    MarketSession,
    Tick,
    Timeframe,
    TradingSession,
)
from tranalyset.market_data.repository import (
    InMemoryStorageBackend,
    MarketDataRepository,
    RetentionPolicy,
    StorageBackend,
)


def _tick(symbol="AAPL", exchange="NASDAQ", timestamp=None, price=100.0, size=1000.0):
    ts = timestamp if timestamp is not None else datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Tick(symbol=symbol, exchange=exchange, timestamp=ts, price=price, size=size)


def _candle(symbol="AAPL", exchange="NASDAQ", timeframe=Timeframe.ONE_MINUTE, timestamp=None):
    ts = timestamp if timestamp is not None else datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
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


class TestInMemoryStorageBackend:
    async def test_append_and_get_tick(self):
        backend = InMemoryStorageBackend()
        tick = _tick()
        await backend.append_tick(tick)

        result = await backend.get_ticks("AAPL", "NASDAQ")
        assert len(result) == 1
        assert result[0] == tick

    async def test_tick_retention_pops_oldest(self):
        policy = RetentionPolicy(max_ticks_per_instrument=3)
        backend = InMemoryStorageBackend(retention_policy=policy)

        for i in range(5):
            await backend.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30 + i, tzinfo=UTC)))

        result = await backend.get_ticks("AAPL", "NASDAQ")
        assert len(result) == 3
        assert result[0].timestamp == datetime(2024, 1, 1, 9, 32, tzinfo=UTC)

    async def test_append_and_get_candle(self):
        backend = InMemoryStorageBackend()
        candle = _candle()
        await backend.append_candle(candle)

        result = await backend.get_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE)
        assert len(result) == 1
        assert result[0] == candle

    async def test_candle_retention_pops_oldest(self):
        policy = RetentionPolicy(max_candles_per_instrument_timeframe=2)
        backend = InMemoryStorageBackend(retention_policy=policy)

        for i in range(4):
            await backend.append_candle(
                _candle(timestamp=datetime(2024, 1, 1, 9, 30 + i, tzinfo=UTC))
            )

        result = await backend.get_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE)
        assert len(result) == 2

    async def test_get_ticks_with_range_filter(self):
        backend = InMemoryStorageBackend()
        for hour in range(3):
            await backend.append_tick(
                _tick(timestamp=datetime(2024, 1, 1, 9 + hour, tzinfo=UTC))
            )

        result = await backend.get_ticks(
            "AAPL", "NASDAQ", start=datetime(2024, 1, 1, 10, tzinfo=UTC)
        )
        assert len(result) == 2

    async def test_get_ticks_with_limit(self):
        backend = InMemoryStorageBackend()
        for i in range(10):
            await backend.append_tick(
                _tick(timestamp=datetime(2024, 1, 1, 9, 30 + i, tzinfo=UTC))
            )

        result = await backend.get_ticks("AAPL", "NASDAQ", limit=3)
        assert len(result) == 3
        assert result[0].timestamp == datetime(2024, 1, 1, 9, 37, tzinfo=UTC)

    async def test_out_of_order_writes_are_returned_in_market_time_order(self):
        backend = InMemoryStorageBackend()
        await backend.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 32, tzinfo=UTC)))
        await backend.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC)))

        ticks = await backend.get_ticks("AAPL", "NASDAQ", limit=1)

        assert ticks[0].timestamp == datetime(2024, 1, 1, 9, 32, tzinfo=UTC)

    async def test_instrument_and_calendar(self):
        backend = InMemoryStorageBackend()
        instrument = InstrumentMetadata(
            symbol="AAPL", exchange="NASDAQ", currency="USD", timezone="America/New_York"
        )
        calendar = MarketCalendar(
            exchange="NASDAQ",
            timezone="America/New_York",
            sessions=[
                TradingSession(
                    exchange="NASDAQ",
                    session_type=MarketSession.REGULAR,
                    start=datetime(2024, 1, 1, 9, 30, tzinfo=UTC),
                    end=datetime(2024, 1, 1, 16, 0, tzinfo=UTC),
                )
            ],
        )

        await backend.append_instrument(instrument)
        await backend.append_calendar(calendar)

        assert await backend.get_instrument("AAPL", "NASDAQ") == instrument
        assert await backend.get_calendar("NASDAQ") == calendar

    async def test_clear(self):
        backend = InMemoryStorageBackend()
        await backend.append_tick(_tick())
        await backend.append_candle(_candle())

        await backend.clear()

        assert await backend.get_ticks("AAPL", "NASDAQ") == ()
        assert await backend.get_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE) == ()


class TestMarketDataRepository:
    async def test_append_and_get_latest_tick(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)

        await repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC)))
        await repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 31, tzinfo=UTC)))

        latest = await repo.get_latest_tick("AAPL", "NASDAQ")
        assert latest.timestamp == datetime(2024, 1, 1, 9, 31, tzinfo=UTC)

    async def test_get_latest_tick_honours_historical_as_of_time(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        await repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC), price=100.0))
        await repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 32, tzinfo=UTC), price=200.0))

        latest = await repo.get_latest_tick(
            "AAPL", "NASDAQ", end=datetime(2024, 1, 1, 9, 31, tzinfo=UTC)
        )

        assert latest is not None
        assert latest.price == 100.0

    async def test_get_ticks_returns_sequence(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)

        await repo.append_tick(_tick())
        result = await repo.get_ticks("AAPL", "NASDAQ")
        assert len(result) == 1

    async def test_get_latest_candle_none_when_empty(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)

        result = await repo.get_latest_candle("AAPL", "NASDAQ", Timeframe.ONE_MINUTE)
        assert result is None

    async def test_multiple_symbols_and_exchanges(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)

        await repo.append_tick(_tick(symbol="AAPL", exchange="NASDAQ"))
        await repo.append_tick(_tick(symbol="MSFT", exchange="NYSE"))

        aapl = await repo.get_ticks("AAPL", "NASDAQ")
        msft = await repo.get_ticks("MSFT", "NYSE")
        assert len(aapl) == 1
        assert len(msft) == 1

    async def test_multiple_timeframes(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)

        for tf in [Timeframe.ONE_MINUTE, Timeframe.FIVE_MINUTES]:
            await repo.append_candle(_candle(timeframe=tf))

        m1 = await repo.get_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE)
        m5 = await repo.get_candles("AAPL", "NASDAQ", Timeframe.FIVE_MINUTES)
        assert len(m1) == 1
        assert len(m5) == 1

    async def test_clear_repository(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)

        await repo.append_tick(_tick())
        await repo.append_candle(_candle())
        await repo.clear()

        assert await repo.get_ticks("AAPL", "NASDAQ") == ()
        assert await repo.get_candles("AAPL", "NASDAQ", Timeframe.ONE_MINUTE) == ()


class TestRetentionPolicy:
    def test_default_values(self):
        policy = RetentionPolicy()
        assert policy.max_ticks_per_instrument == 100_000
        assert policy.max_candles_per_instrument_timeframe == 10_000

    def test_is_expired_with_none(self):
        policy = RetentionPolicy(max_age_seconds=None)
        now = datetime(2024, 1, 2, tzinfo=UTC)
        assert policy.is_expired(datetime(2024, 1, 1, tzinfo=UTC), now) is False

    def test_is_expired_with_age(self):
        policy = RetentionPolicy(max_age_seconds=3600)
        now = datetime(2024, 1, 1, 10, 1, tzinfo=UTC)
        assert policy.is_expired(datetime(2024, 1, 1, 9, tzinfo=UTC), now) is True
        assert policy.is_expired(datetime(2024, 1, 1, 10, 0, tzinfo=UTC), now) is False
