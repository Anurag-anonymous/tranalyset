"""Market data repository implementation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from tranalyset.market_data.models import (
    Candle,
    InstrumentMetadata,
    MarketCalendar,
    Tick,
    Timeframe,
)

logger = logging.getLogger("tranalyset.market_data")


class DataKeyError(KeyError):
    pass


class StorageBackend:
    """Async storage backend interface for market data.

    Implement this interface to provide alternative persistence layers
    (e.g., database, message queue, external API).
    """

    async def append_tick(self, tick: Tick) -> None:
        raise NotImplementedError

    async def append_candle(self, candle: Candle) -> None:
        raise NotImplementedError

    async def append_instrument(self, instrument: InstrumentMetadata) -> None:
        raise NotImplementedError

    async def append_calendar(self, calendar: MarketCalendar) -> None:
        raise NotImplementedError

    async def get_ticks(
        self,
        symbol: str,
        exchange: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Tick]:
        raise NotImplementedError

    async def get_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Candle]:
        raise NotImplementedError

    async def get_instrument(self, symbol: str, exchange: str) -> InstrumentMetadata | None:
        raise NotImplementedError

    async def get_calendar(self, exchange: str) -> MarketCalendar | None:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


@dataclass
class RetentionPolicy:
    """Configurable data retention policy."""
    max_ticks_per_instrument: int = 100_000
    max_candles_per_instrument_timeframe: int = 10_000
    max_instruments: int = 10_000
    max_calendars: int = 100
    max_age_seconds: float | None = None  # None = no time-based expiry

    def is_expired(self, timestamp: datetime, now: datetime) -> bool:
        if self.max_age_seconds is None:
            return False
        age = (now - timestamp).total_seconds()
        return age > self.max_age_seconds


class InMemoryStorageBackend(StorageBackend):
    """Thread-safe in-memory storage backend.

    Uses asyncio locks to ensure safe concurrent access.
    """

    def __init__(self, retention_policy: RetentionPolicy | None = None) -> None:
        self._policy = retention_policy or RetentionPolicy()
        self._lock = asyncio.Lock()

        # symbol:exchange -> deque[Tick]
        self._ticks: dict[str, deque[Tick]] = defaultdict(deque)
        # (symbol, exchange, timeframe) -> deque[Candle]
        self._candles: dict[tuple[str, str, Timeframe], deque[Candle]] = defaultdict(deque)
        # symbol:exchange -> InstrumentMetadata
        self._instruments: dict[str, InstrumentMetadata] = {}
        # exchange -> MarketCalendar
        self._calendars: dict[str, MarketCalendar] = {}

    async def append_tick(self, tick: Tick) -> None:
        async with self._lock:
            key = f"{tick.symbol}:{tick.exchange}"
            self._ticks[key].append(tick)
            self._ticks[key] = deque(sorted(self._ticks[key], key=lambda item: item.timestamp))
            if len(self._ticks[key]) > self._policy.max_ticks_per_instrument:
                self._ticks[key].popleft()

    async def append_candle(self, candle: Candle) -> None:
        async with self._lock:
            key = (candle.symbol, candle.exchange, candle.timeframe)
            self._candles[key].append(candle)
            self._candles[key] = deque(
                sorted(self._candles[key], key=lambda item: item.timestamp)
            )
            if len(self._candles[key]) > self._policy.max_candles_per_instrument_timeframe:
                self._candles[key].popleft()

    async def append_instrument(self, instrument: InstrumentMetadata) -> None:
        async with self._lock:
            key = f"{instrument.symbol}:{instrument.exchange}"
            self._instruments[key] = instrument

    async def append_calendar(self, calendar: MarketCalendar) -> None:
        async with self._lock:
            self._calendars[calendar.exchange] = calendar

    async def get_ticks(
        self,
        symbol: str,
        exchange: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Tick]:
        async with self._lock:
            key = f"{symbol}:{exchange}"
            ticks = list(self._ticks.get(key, []))

        if start is not None:
            ticks = [t for t in ticks if t.timestamp >= start]
        if end is not None:
            ticks = [t for t in ticks if t.timestamp <= end]
        if limit is not None:
            ticks = ticks[-limit:]
        return tuple(ticks)

    async def get_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Candle]:
        async with self._lock:
            key = (symbol, exchange, timeframe)
            candles = list(self._candles.get(key, []))

        if start is not None:
            candles = [c for c in candles if c.timestamp >= start]
        if end is not None:
            candles = [c for c in candles if c.timestamp <= end]
        if limit is not None:
            candles = candles[-limit:]
        return tuple(candles)

    async def get_instrument(self, symbol: str, exchange: str) -> InstrumentMetadata | None:
        async with self._lock:
            return self._instruments.get(f"{symbol}:{exchange}")

    async def get_calendar(self, exchange: str) -> MarketCalendar | None:
        async with self._lock:
            return self._calendars.get(exchange)

    async def clear(self) -> None:
        async with self._lock:
            self._ticks.clear()
            self._candles.clear()
            self._instruments.clear()
            self._calendars.clear()


class MarketDataRepository:
    """Thread-safe repository for market data.

    Provides efficient read/write access to ticks, candles, instruments,
    calendars, and corporate actions. Uses an async storage backend for
    all persistence operations.

    This is shared infrastructure, not a plugin. It is exposed through
    the kernel's dependency injection container.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self._lock = asyncio.Lock()
        self._tick_idempotency_keys: set[str] = set()
        self._candle_idempotency_keys: set[str] = set()

    async def append_tick(self, tick: Tick) -> bool:
        """Persist a tick once, returning whether this call added a new record."""
        key = _tick_idempotency_key(tick)
        async with self._lock:
            if key in self._tick_idempotency_keys:
                return False
            await self._backend.append_tick(tick)
            self._tick_idempotency_keys.add(key)
        logger.debug("Tick appended %s %s @ %s", tick.symbol, tick.exchange, tick.timestamp)
        return True

    async def append_candle(self, candle: Candle) -> bool:
        """Persist a candle once, returning whether this call added a new record."""
        key = _candle_idempotency_key(candle)
        async with self._lock:
            if key in self._candle_idempotency_keys:
                return False
            await self._backend.append_candle(candle)
            self._candle_idempotency_keys.add(key)
        logger.debug(
            "Candle appended %s %s %s @ %s",
            candle.symbol,
            candle.exchange,
            candle.timeframe,
            candle.timestamp,
        )
        return True

    async def append_instrument(self, instrument: InstrumentMetadata) -> None:
        """Register or update instrument metadata."""
        async with self._lock:
            await self._backend.append_instrument(instrument)
        logger.debug("Instrument registered %s:%s", instrument.symbol, instrument.exchange)

    async def append_calendar(self, calendar: MarketCalendar) -> None:
        """Register or update a market calendar."""
        async with self._lock:
            await self._backend.append_calendar(calendar)
        logger.debug("Calendar registered %s", calendar.exchange)

    async def get_latest_tick(
        self, symbol: str, exchange: str, *, end: datetime | None = None
    ) -> Tick | None:
        """Get the most recent tick for a symbol/exchange pair."""
        ticks = await self.get_ticks(symbol, exchange, end=end, limit=1)
        if ticks:
            return ticks[-1]
        return None

    async def get_latest_candle(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        *,
        end: datetime | None = None,
    ) -> Candle | None:
        """Get the most recent candle for a symbol/exchange/timeframe."""
        candles = await self.get_candles(symbol, exchange, timeframe, end=end, limit=1)
        if candles:
            return candles[-1]
        return None

    async def get_ticks(
        self,
        symbol: str,
        exchange: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Tick]:
        """Get ticks for a symbol/exchange pair with optional time range."""
        return await self._backend.get_ticks(symbol, exchange, start=start, end=end, limit=limit)

    async def get_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Candle]:
        """Get candles for a symbol/exchange/timeframe with optional time range."""
        return await self._backend.get_candles(
            symbol, exchange, timeframe, start=start, end=end, limit=limit
        )

    async def get_timeframe(self, symbol: str, exchange: str) -> list[Timeframe]:
        """Get available timeframes for a symbol/exchange pair."""
        # Implementation depends on backend; for now, return common timeframes
        # The backend can optimize this query
        return [
            tf
            for tf in Timeframe
            if not await self._backend.get_candles(symbol, exchange, tf, limit=0)
        ]

    async def get_range(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe,
    ) -> Sequence[Candle]:
        """Get candles for a specific time range (alias for get_candles)."""
        return await self.get_candles(symbol, exchange, timeframe, start=start, end=end)

    async def get_instrument(self, symbol: str, exchange: str) -> InstrumentMetadata | None:
        """Get instrument metadata."""
        return await self._backend.get_instrument(symbol, exchange)

    async def get_calendar(self, exchange: str) -> MarketCalendar | None:
        """Get market calendar for an exchange."""
        return await self._backend.get_calendar(exchange)

    async def clear(self) -> None:
        """Clear all data from the repository."""
        async with self._lock:
            await self._backend.clear()
            self._tick_idempotency_keys.clear()
            self._candle_idempotency_keys.clear()
        logger.warning("Market data repository cleared.")


def _tick_idempotency_key(tick: Tick) -> str:
    return _idempotency_digest(
        {
            "symbol": tick.symbol,
            "exchange": tick.exchange,
            "timestamp": tick.timestamp.astimezone(UTC).isoformat(),
            "price": tick.price,
            "size": tick.size,
            "side": tick.side,
            "metadata": dict(tick.metadata),
        }
    )


def _candle_idempotency_key(candle: Candle) -> str:
    return _idempotency_digest(
        {
            "symbol": candle.symbol,
            "exchange": candle.exchange,
            "timeframe": candle.timeframe.value,
            "timestamp": candle.timestamp.astimezone(UTC).isoformat(),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "trades": candle.trades,
            "metadata": dict(candle.metadata),
        }
    )


def _idempotency_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
