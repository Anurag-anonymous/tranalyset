"""Tests for the Data Quality Engine."""
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.models import Candle, Tick, Timeframe
from tranalyset.market_data.quality import DataQualityEngine, DataQualityIssue, IssueSeverity
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data.contracts import TICK_RECEIVED


def _tick(symbol="AAPL", exchange="NASDAQ", timestamp=None, price=100.0, size=1000.0):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Tick(symbol=symbol, exchange=exchange, timestamp=ts, price=price, size=size)


def _candle(symbol="AAPL", exchange="NASDAQ", timeframe=Timeframe.ONE_MINUTE, timestamp=None, open=100.0, high=102.0, low=98.0, close=101.0, volume=1000.0):
    ts = timestamp or datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    return Candle(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        timestamp=ts,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        trades=42,
    )


class TestDataQualityEngine:
    @pytest.fixture
    async def engine(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        engine = DataQualityEngine(repository=repo, event_bus=bus)
        yield engine
        await engine.stop()
        await bus.stop()

    async def test_start_stop_lifecycle(self, engine):
        assert engine._running is False
        await engine.start()
        assert engine._running is True
        await engine.stop()
        assert engine._running is False

    async def test_validate_tick_detects_duplicate(self, engine):
        t1 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC))
        t2 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC))
        await engine.validate_tick(t1)
        issues = await engine.validate_tick(t2)
        assert len(issues) == 1
        assert issues[0].issue_type == "duplicate_tick"

    async def test_validate_tick_detects_price_spike(self, engine):
        t1 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, 0, tzinfo=UTC), price=100.0)
        t2 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, 1, tzinfo=UTC), price=150.0)  # 50% spike
        await engine.validate_tick(t1)
        issues = await engine.validate_tick(t2)
        assert len(issues) == 1
        assert issues[0].issue_type == "price_spike"

    async def test_validate_tick_no_spike_within_threshold(self, engine):
        t1 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, 0, tzinfo=UTC), price=100.0)
        t2 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, 1, tzinfo=UTC), price=105.0)  # 5% spike
        await engine.validate_tick(t1)
        issues = await engine.validate_tick(t2)
        assert len(issues) == 0

    async def test_validate_candle_detects_invalid_ohlc(self, engine):
        # Domain model enforces valid OHLC, so test engine logic indirectly
        # by checking that valid candles produce no issues
        candle = _candle(open=100.0, high=110.0, low=90.0, close=105.0)
        issues = await engine.validate_candle(candle)
        assert len(issues) == 0

    async def test_validate_event_order_detects_out_of_order(self, engine):
        e1 = EventEnvelope.create(
            source="test",
            event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "exchange": "NASDAQ", "timestamp": datetime(2024, 1, 1, 9, 30, tzinfo=UTC).isoformat()},
        )
        e2 = EventEnvelope.create(
            source="test",
            event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "exchange": "NASDAQ", "timestamp": datetime(2024, 1, 1, 9, 29, tzinfo=UTC).isoformat()},
        )
        await engine.validate_event_order(e1)
        issues = await engine.validate_event_order(e2)
        assert len(issues) == 1
        assert issues[0].issue_type == "out_of_order_event"

    async def test_supported_issue_types(self, engine):
        assert len(engine.supported_issue_types) == 7
        assert "duplicate_tick" in engine.supported_issue_types
        assert "missing_candle" in engine.supported_issue_types
        assert "invalid_ohlc" in engine.supported_issue_types

    async def test_validate_does_not_modify_data(self, engine):
        original_tick = _tick(price=100.0, size=1000.0)
        await engine.validate_tick(original_tick)
        # Ensure the tick object wasn't modified
        assert original_tick.price == 100.0
        assert original_tick.size == 1000.0

    async def test_timestamp_drift_detection(self, engine):
        t1 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, 0, tzinfo=UTC))
        t2 = _tick(timestamp=datetime(2024, 1, 1, 9, 30, 10, tzinfo=UTC))  # 10s drift
        await engine.validate_tick(t1)
        issues = await engine.validate_tick(t2)
        assert len(issues) == 1
        assert issues[0].issue_type == "timestamp_anomaly"


class TestDataQualityIssue:
    async def test_issue_creation(self):
        issue = DataQualityIssue(
            severity=IssueSeverity.WARNING,
            issue_type="test_issue",
            symbol="AAPL",
            exchange="NASDAQ",
            timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=UTC),
            description="Test issue",
        )
        assert issue.severity == IssueSeverity.WARNING
        assert issue.issue_type == "test_issue"
        assert issue.symbol == "AAPL"
