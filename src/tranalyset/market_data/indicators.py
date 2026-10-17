"""Indicator Cache Service for storing and retrieving computed technical indicators."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.event_bus import AsyncEventBus, Subscription
from tranalyset.market_data.models import Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED

logger = logging.getLogger("tranalyset.market_data.indicators")

SUPPORTED_INDICATORS = [
    "EMA",
    "SMA",
    "VWAP",
    "MACD",
    "RSI",
    "ATR",
    "ADX",
    "Supertrend",
    "Ichimoku",
    "BollingerBands",
]


@dataclass(frozen=True, slots=True)
class IndicatorKey:
    """Unique key for an indicator cache entry."""
    symbol: str
    exchange: str
    timeframe: Timeframe
    indicator: str
    period: int | None = None


@dataclass(frozen=True, slots=True)
class IndicatorValue:
    """A cached indicator value with metadata."""
    key: IndicatorKey
    timestamp: datetime
    value: float | tuple[float, ...] | None
    computed_at: datetime
    version: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class IndicatorCache:
    """Infrastructure service for caching technical indicator values.

    This is NOT a plugin. It is shared infrastructure exposed through DI.
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        event_bus: AsyncEventBus,
        default_ttl_seconds: float = 300.0,
    ) -> None:
        self._repo = repository
        self._bus = event_bus
        self._default_ttl = default_ttl_seconds
        self._cache: dict[IndicatorKey, list[IndicatorValue]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._running = False
        self._subscription: Subscription | None = None

    @property
    def supported_indicators(self) -> list[str]:
        return list(SUPPORTED_INDICATORS)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        async def on_candle(event: EventEnvelope) -> None:
            await self._handle_candle(event)

        self._subscription = await self._bus.subscribe(
            CANDLE_CLOSED,
            on_candle,
        )
        logger.info("Indicator cache service started.")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._subscription:
            await self._bus.unsubscribe(self._subscription)
        logger.info("Indicator cache service stopped.")

    async def get(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        indicator: str,
        period: int | None = None,
        timestamp: datetime | None = None,
    ) -> IndicatorValue | None:
        key = IndicatorKey(symbol=symbol, exchange=exchange, timeframe=timeframe, indicator=indicator, period=period)
        async with self._lock:
            values = self._cache.get(key, [])
            if not values:
                return None
            if timestamp is not None:
                candidates = [v for v in values if v.timestamp <= timestamp]
                if not candidates:
                    return None
                return max(candidates, key=lambda v: v.timestamp)
            return values[-1]

    async def put(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        indicator: str,
        timestamp: datetime,
        value: float | tuple[float, ...] | None,
        *,
        period: int | None = None,
        version: str = "1.0",
        metadata: Mapping[str, Any] | None = None,
        ttl: float | None = None,
    ) -> None:
        key = IndicatorKey(symbol=symbol, exchange=exchange, timeframe=timeframe, indicator=indicator, period=period)
        entry = IndicatorValue(
            key=key,
            timestamp=timestamp,
            value=value,
            computed_at=timestamp.astimezone(UTC),
            version=version,
            metadata=metadata or {},
        )
        async with self._lock:
            self._cache[key].append(entry)
            self._cache[key].sort(key=lambda v: v.timestamp)
            if ttl is None:
                ttl = self._default_ttl
            cutoff = timestamp.astimezone(UTC) - timedelta(seconds=ttl)
            self._cache[key] = [v for v in self._cache[key] if v.timestamp >= cutoff]
        logger.debug("Cached %s for %s:%s %s", indicator, symbol, exchange, timestamp)

    async def invalidate(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        indicator: str,
        period: int | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        async with self._lock:
            keys_to_remove = []
            for key, values in self._cache.items():
                if key.symbol != symbol or key.exchange != exchange or key.timeframe != timeframe:
                    continue
                if indicator != "*" and key.indicator != indicator:
                    continue
                if period is not None and key.period != period:
                    continue
                if timestamp is not None:
                    self._cache[key] = [v for v in values if v.timestamp != timestamp]
                    if not self._cache[key]:
                        keys_to_remove.append(key)
                else:
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                self._cache.pop(key, None)
        logger.debug("Invalidated %s for %s:%s %s", indicator, symbol, exchange, timeframe)

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()
        logger.warning("Indicator cache cleared.")

    async def _handle_candle(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = payload.get("symbol")
        exchange = payload.get("venue")
        timeframe = payload.get("timeframe")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("symbol must be a non-empty string.")
        if not isinstance(exchange, str) or not exchange:
            raise ValueError("venue must be a non-empty string.")
        if not isinstance(timeframe, str) or not timeframe:
            raise ValueError("timeframe must be a non-empty string.")
        await self.invalidate(symbol, exchange, Timeframe(timeframe), "*")

    async def compute_if_missing(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        indicator: str,
        period: int | None,
        timestamp: datetime,
        computer: Any,
    ) -> Any:
        cached = await self.get(symbol, exchange, timeframe, indicator, period=period, timestamp=timestamp)
        if cached is not None:
            return cached.value
        value = await computer(symbol, exchange, timeframe, timestamp)
        await self.put(symbol, exchange, timeframe, indicator, timestamp, value, period=period)
        return value
