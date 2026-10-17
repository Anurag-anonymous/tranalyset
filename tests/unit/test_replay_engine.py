"""Tests for the Replay Engine."""
import asyncio
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.models import Candle, Tick, Timeframe
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.market_data.replay import PlaybackMode, ReplayEngine, ReplayState
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED


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


class TestReplayEngine:
    @pytest.fixture
    async def engine(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        engine = ReplayEngine(repository=repo, event_bus=bus)
        yield engine
        await engine.stop()
        await bus.stop()

    async def test_initial_state(self, engine):
        assert engine.state == ReplayState.IDLE
        assert engine.playback_mode == PlaybackMode.REALTIME

    async def test_load_session_counts_events(self, engine):
        # Pre-populate repository
        for i in range(5):
            await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30 + i, tzinfo=UTC)))
        count = await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 35, tzinfo=UTC))
        assert count == 5

    async def test_load_session_with_timeframe_loads_candles(self, engine):
        for i in range(3):
            await engine._repo.append_candle(_candle(timestamp=datetime(2024, 1, 1, 9, 30 + i, tzinfo=UTC)))
        count = await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 32, tzinfo=UTC), timeframe=Timeframe.ONE_MINUTE)
        assert count == 3

    async def test_step_publishes_single_event(self, engine):
        await engine._repo.append_tick(_tick())
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))

        published = []

        async def collect(event: EventEnvelope) -> None:
            published.append(event)

        await engine._bus.subscribe(TICK_RECEIVED, collect)
        await engine.step()
        await engine._bus.wait_until_idle()

        assert len(published) == 1
        assert published[0].payload["symbol"] == "AAPL"
        # Single-event session completes immediately after step
        assert engine.state in {ReplayState.IDLE, ReplayState.COMPLETED}

    async def test_step_advances_index(self, engine):
        await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC)))
        await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 31, tzinfo=UTC)))
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))
        assert engine._current_index == 0

        await engine.step()
        assert engine._current_index == 1

        await engine.step()
        assert engine._current_index == 2
        assert engine.state == ReplayState.COMPLETED

    async def test_pause_resume(self, engine):
        await engine._repo.append_tick(_tick())
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))

        await engine.play()
        assert engine.state == ReplayState.PLAYING

        await engine.pause()
        assert engine.state == ReplayState.PAUSED

        await engine.resume()
        assert engine.state == ReplayState.PLAYING

        await engine.stop()
        assert engine.state == ReplayState.STOPPED

    async def test_stop_resets_state(self, engine):
        await engine._repo.append_tick(_tick())
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))
        await engine.play()
        await engine.stop()
        assert engine._current_index == 0
        assert engine.state == ReplayState.STOPPED

    async def test_seek_valid_index(self, engine):
        await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC)))
        await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 31, tzinfo=UTC)))
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))
        await engine.seek(1)
        assert engine._current_index == 1

    async def test_seek_invalid_index_raises(self, engine):
        with pytest.raises(IndexError):
            await engine.seek(999)

    async def test_progress_returns_fraction(self, engine):
        await engine._repo.append_tick(_tick())
        await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 31, tzinfo=UTC)))
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))
        await engine.step()
        progress = await engine.progress()
        assert progress == 0.5

    async def test_playback_mode_speed(self, engine):
        engine.playback_mode = PlaybackMode.SPEED_10X
        assert engine.playback_mode == PlaybackMode.SPEED_10X

    async def test_empty_session_no_play(self, engine):
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))
        await engine.play()
        assert engine.state == ReplayState.IDLE  # No events to play

    async def test_replay_sorted_by_timestamp(self, engine):
        await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 31, tzinfo=UTC)))
        await engine._repo.append_tick(_tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC)))
        await engine.load_session("AAPL", "NASDAQ", datetime(2024, 1, 1, 9, 30, tzinfo=UTC), datetime(2024, 1, 1, 9, 31, tzinfo=UTC))
        assert engine._events[0].timestamp == datetime(2024, 1, 1, 9, 30, tzinfo=UTC)

    async def test_candle_replay_uses_close_time_and_does_not_replay_ticks(self, engine):
        tick_time = datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
        await engine._repo.append_tick(_tick(timestamp=tick_time))
        await engine._repo.append_candle(_candle(timestamp=tick_time))
        await engine.load_session(
            "AAPL", "NASDAQ", tick_time, datetime(2024, 1, 1, 9, 31, tzinfo=UTC),
            timeframe=Timeframe.ONE_MINUTE,
        )
        published: list[EventEnvelope] = []

        async def collect(event: EventEnvelope) -> None:
            published.append(event)

        await engine._bus.subscribe(CANDLE_CLOSED, collect)
        await engine.step()
        await engine._bus.wait_until_idle()

        assert len(engine._events) == 1
        assert published[0].timestamp == datetime(2024, 1, 1, 9, 31, tzinfo=UTC)
        assert published[0].payload["opened_at"] == tick_time.isoformat()
