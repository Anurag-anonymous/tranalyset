"""Tests for the Market Calendar Service."""
from datetime import UTC, datetime

import pytest

from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.calendar import MarketCalendarService, SessionWindow
from tranalyset.market_data.models import MarketCalendar, MarketSession, TradingSession
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data.contracts import MARKET_CLOSED, MARKET_OPENED


def _calendar(exchange="NASDAQ", sessions=None, holidays=None):
    if sessions is None:
        sessions = [
            TradingSession(
                exchange=exchange,
                session_type=MarketSession.PRE_MARKET,
                start=datetime(2024, 1, 1, 8, 0, tzinfo=UTC),
                end=datetime(2024, 1, 1, 9, 30, tzinfo=UTC),
            ),
            TradingSession(
                exchange=exchange,
                session_type=MarketSession.REGULAR,
                start=datetime(2024, 1, 1, 9, 30, tzinfo=UTC),
                end=datetime(2024, 1, 1, 16, 0, tzinfo=UTC),
            ),
            TradingSession(
                exchange=exchange,
                session_type=MarketSession.POST_MARKET,
                start=datetime(2024, 1, 1, 16, 0, tzinfo=UTC),
                end=datetime(2024, 1, 1, 17, 0, tzinfo=UTC),
            ),
        ]
    return MarketCalendar(
        exchange=exchange,
        timezone="America/New_York",
        sessions=sessions,
        holidays=holidays or [],
    )


class TestMarketCalendarService:
    @pytest.fixture
    async def service(self):
        backend = InMemoryStorageBackend()
        repo = MarketDataRepository(backend)
        bus = AsyncEventBus()
        await bus.start()
        service = MarketCalendarService(repository=repo, event_bus=bus)
        yield service
        await service.stop()
        await bus.stop()

    async def test_register_and_get_calendar(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)
        retrieved = await service.get_calendar("NASDAQ")
        assert retrieved is not None
        assert retrieved.exchange == "NASDAQ"
        assert len(retrieved.sessions) == 3

    async def test_start_loads_default_exchanges(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)
        await service.start()
        assert "NASDAQ" in service.supported_exchanges
        await service.stop()

    async def test_is_market_open_during_regular_session(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        result = await service.is_market_open("NASDAQ", timestamp=ts)
        assert result is True

    async def test_is_market_open_outside_sessions(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 1, 7, 59, tzinfo=UTC)  # Before first session
        result = await service.is_market_open("NASDAQ", timestamp=ts)
        assert result is False

    async def test_is_market_closed_on_weekend(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 6, 10, 0, tzinfo=UTC)  # Saturday
        result = await service.is_market_open("NASDAQ", timestamp=ts)
        assert result is False

    async def test_is_market_closed_on_holiday(self, service):
        holiday = datetime(2024, 1, 1, tzinfo=UTC)
        calendar = _calendar(exchange="NASDAQ", holidays=[holiday])
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        result = await service.is_market_open("NASDAQ", timestamp=ts)
        assert result is False

    async def test_get_current_session_returns_regular(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        session = await service.get_current_session("NASDAQ", timestamp=ts)
        assert session == MarketSession.REGULAR

    async def test_get_current_session_returns_none_when_closed(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 1, 7, 59, tzinfo=UTC)
        session = await service.get_current_session("NASDAQ", timestamp=ts)
        assert session is None

    async def test_get_next_session(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 1, 7, 59, tzinfo=UTC)
        next_session = await service.get_next_session("NASDAQ", timestamp=ts)
        assert next_session is not None
        assert next_session.session_type == MarketSession.PRE_MARKET

    async def test_session_window_detection(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)

        ts = datetime(2024, 1, 1, 9, 0, tzinfo=UTC)
        window = service._find_current_session(calendar, ts)
        assert window is not None
        assert window.session_type == MarketSession.PRE_MARKET

    async def test_supported_exchanges(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)
        calendar2 = _calendar(exchange="NYSE")
        await service.register_calendar(calendar2)

        assert "NASDAQ" in service.supported_exchanges
        assert "NYSE" in service.supported_exchanges
        assert len(service.supported_exchanges) == 2

    async def test_stop_cancels_monitor_task(self, service):
        calendar = _calendar(exchange="NASDAQ")
        await service.register_calendar(calendar)
        await service.start()
        assert hasattr(service, '_monitor_task')
        await service.stop()
        assert service._monitor_task.done()


class TestCalendarEventTypes:
    async def test_market_session_event_types_are_public_contracts(self):
        assert str(MARKET_OPENED) == "MarketOpened"
        assert str(MARKET_CLOSED) == "MarketClosed"


class TestSessionWindow:
    async def test_session_window_creation(self):
        window = SessionWindow(
            session_type=MarketSession.REGULAR,
            start=datetime(2024, 1, 1, 9, 30, tzinfo=UTC),
            end=datetime(2024, 1, 1, 16, 0, tzinfo=UTC),
            is_half_day=False,
        )
        assert window.session_type == MarketSession.REGULAR
        assert window.is_half_day is False
