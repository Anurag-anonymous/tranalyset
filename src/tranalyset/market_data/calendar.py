"""Market Calendar Service for tracking exchange trading sessions."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus, Subscription
from tranalyset.market_data.models import MarketCalendar, MarketSession
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.market_data.contracts import MARKET_CLOSED, MARKET_OPENED, MarketSessionPayload

logger = logging.getLogger("tranalyset.market_data.calendar")


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """A trading session window with start/end times."""
    session_type: MarketSession
    start: datetime
    end: datetime
    is_half_day: bool = False


class MarketCalendarService:
    """Infrastructure service for tracking exchange trading sessions.

    This is NOT a plugin. It is shared infrastructure exposed through DI.
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        event_bus: AsyncEventBus,
    ) -> None:
        self._repo = repository
        self._bus = event_bus
        self._calendars: dict[str, MarketCalendar] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._subscriptions: list[Subscription] = []
        self._current_sessions: dict[str, SessionWindow | None] = {}

    @property
    def supported_exchanges(self) -> list[str]:
        return list(self._calendars.keys())

    async def start(self) -> None:
        """Start the calendar service and subscribe to time ticks."""
        if self._running:
            return
        self._running = True

        # Load calendars from repository
        for exchange in ["NASDAQ", "NYSE"]:  # Default exchanges
            calendar = await self._repo.get_calendar(exchange)
            if calendar:
                self._calendars[exchange] = calendar

        self._subscriptions = [
            await self._bus.subscribe(MARKET_OPENED, self._handle_market_opened),
            await self._bus.subscribe(MARKET_CLOSED, self._handle_market_closed),
        ]

        # Start monitoring task
        self._monitor_task = asyncio.create_task(self._monitor_sessions())
        logger.info("Market calendar service started.")

    async def stop(self) -> None:
        """Stop the calendar service."""
        if not self._running:
            return
        self._running = False
        if hasattr(self, '_monitor_task'):
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        for subscription in self._subscriptions:
            await self._bus.unsubscribe(subscription)
        self._subscriptions.clear()
        logger.info("Market calendar service stopped.")

    async def register_calendar(self, calendar: MarketCalendar) -> None:
        """Register a market calendar for an exchange."""
        async with self._lock:
            self._calendars[calendar.exchange] = calendar
            await self._repo.append_calendar(calendar)
        logger.info("Calendar registered for %s", calendar.exchange)

    async def get_calendar(self, exchange: str) -> MarketCalendar | None:
        """Get the registered calendar for an exchange."""
        return self._calendars.get(exchange)

    async def is_market_open(self, exchange: str, timestamp: datetime | None = None) -> bool:
        """Check if the market is currently open for an exchange."""
        if timestamp is None:
            timestamp = datetime.now(UTC)
        calendar = self._calendars.get(exchange)
        if calendar is None:
            return False
        return self._is_in_session(calendar, timestamp)

    async def get_current_session(self, exchange: str, timestamp: datetime | None = None) -> MarketSession | None:
        """Get the current trading session for an exchange."""
        if timestamp is None:
            timestamp = datetime.now(UTC)
        calendar = self._calendars.get(exchange)
        if calendar is None:
            return None
        window = self._find_current_session(calendar, timestamp)
        return window.session_type if window else None

    async def get_next_session(self, exchange: str, timestamp: datetime | None = None) -> SessionWindow | None:
        """Get the next upcoming session for an exchange."""
        if timestamp is None:
            timestamp = datetime.now(UTC)
        calendar = self._calendars.get(exchange)
        if calendar is None:
            return None
        return self._find_next_session(calendar, timestamp)

    def _is_in_session(self, calendar: MarketCalendar, timestamp: datetime) -> bool:
        """Check if a timestamp falls within any trading session."""
        window = self._find_current_session(calendar, timestamp)
        return window is not None

    def _find_current_session(self, calendar: MarketCalendar, timestamp: datetime) -> SessionWindow | None:
        """Find the current session window for a timestamp."""
        # Check if it's a weekend
        if timestamp.weekday() >= 5:  # Saturday=5, Sunday=6
            return None

        # Check if it's a holiday
        for holiday in calendar.holidays:
            if holiday.date() == timestamp.date():
                return None

        # Check sessions
        for session in calendar.sessions:
            if session.start <= timestamp <= session.end:
                return SessionWindow(
                    session_type=session.session_type,
                    start=session.start,
                    end=session.end,
                    is_half_day=False,  # Could be enhanced with half-day detection
                )
        return None

    def _find_next_session(self, calendar: MarketCalendar, timestamp: datetime) -> SessionWindow | None:
        """Find the next upcoming session."""
        for session in calendar.sessions:
            if session.start > timestamp:
                return SessionWindow(
                    session_type=session.session_type,
                    start=session.start,
                    end=session.end,
                )
        return None

    async def _monitor_sessions(self) -> None:
        """Monitor session changes and publish events."""
        while self._running:
            try:
                await asyncio.sleep(1)  # Check every second
                for exchange, calendar in self._calendars.items():
                    now = datetime.now(UTC)
                    current = self._find_current_session(calendar, now)
                    previous = self._current_sessions.get(exchange)

                    if current is None and previous is not None:
                        # Market closed
                        await self._publish_session_event(MARKET_CLOSED, exchange, now)
                        self._current_sessions[exchange] = None
                    elif current is not None and previous is None:
                        # Market opened
                        await self._publish_session_event(MARKET_OPENED, exchange, now)
                        self._current_sessions[exchange] = current
                    elif current is not None and previous is not None:
                        # Check if session changed
                        if current.session_type != previous.session_type:
                            self._current_sessions[exchange] = current
            except asyncio.CancelledError:
                break
            except Exception as error:
                logger.error("Error in session monitor: %s", error)

    async def _handle_market_opened(self, event: EventEnvelope) -> None:
        if event.source != "market_calendar":
            await self._record_session_event(event, opened=True)

    async def _handle_market_closed(self, event: EventEnvelope) -> None:
        if event.source != "market_calendar":
            await self._record_session_event(event, opened=False)

    async def _record_session_event(self, event: EventEnvelope, *, opened: bool) -> None:
        market = event.payload.get("market")
        occurred_at = event.payload.get("occurred_at")
        if not isinstance(market, str) or not market:
            raise ValueError("market must be a non-empty string.")
        if not isinstance(occurred_at, str):
            raise ValueError("occurred_at must be a timezone-aware ISO timestamp.")
        timestamp = datetime.fromisoformat(occurred_at)
        if timestamp.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware.")
        self._current_sessions[market] = self._find_current_session(self._calendars[market], timestamp.astimezone(UTC)) if opened and market in self._calendars else None

    async def _publish_session_event(self, event_type: EventType, exchange: str, timestamp: datetime) -> None:
        """Publish the canonical market-session contract."""
        payload = MarketSessionPayload(market=exchange, occurred_at=timestamp, venue=exchange).to_payload()
        await self._bus.publish(
            EventEnvelope.create(
                source="market_calendar",
                event_type=event_type,
                payload=payload,
                timestamp=timestamp,
            )
        )
        logger.debug("Published %s for %s", event_type, exchange)
