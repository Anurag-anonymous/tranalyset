"""Timeframe aggregation engine for generating higher timeframe candles."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.event_bus import AsyncEventBus, Subscription
from tranalyset.market_data.models import Candle, Tick, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.market_data.contracts import (
    CANDLE_CLOSED,
    TICK_RECEIVED,
    TIMEFRAME_CANDLE_CLOSED,
    CandleClosedPayload,
)

logger = logging.getLogger("tranalyset.market_data.aggregator")

# Supported aggregation timeframes
AGGREGATION_TIMEFRAMES = [
    Timeframe.ONE_MINUTE,
    Timeframe.THREE_MINUTES,
    Timeframe.FIVE_MINUTES,
    Timeframe.TEN_MINUTES,
    Timeframe.FIFTEEN_MINUTES,
    Timeframe.THIRTY_MINUTES,
    Timeframe.FOUR_HOURS,
    Timeframe.ONE_HOUR,
    Timeframe.ONE_DAY,
    Timeframe.ONE_WEEK,
    Timeframe.ONE_MONTH,
]


@dataclass(frozen=True, slots=True)
class AggregationKey:
    """Unique key for an aggregation state."""
    symbol: str
    exchange: str
    timeframe: Timeframe


@dataclass(slots=True)
class _CandleBuilder:
    """Incremental candle builder for a single aggregation period."""
    timestamp: datetime  # Period start time
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    trades: int = 0
    tick_count: int = 0
    complete: bool = False

    def apply_tick(self, tick: Tick) -> None:
        """Apply a tick to the builder."""
        if self.tick_count == 0:
            self.open = tick.price
            self.high = tick.price
            self.low = tick.price
            self.close = tick.price
        else:
            self.high = max(self.high, tick.price)
            self.low = min(self.low, tick.price)
            self.close = tick.price
        self.volume += tick.size
        self.trades += 1
        self.tick_count += 1

    def apply_candle(self, candle: Candle) -> None:
        """Apply a completed lower timeframe candle."""
        if self.tick_count == 0:
            self.open = candle.open
            self.high = candle.high
            self.low = candle.low
            self.close = candle.close
        else:
            self.high = max(self.high, candle.high)
            self.low = min(self.low, candle.low)
            self.close = candle.close
        self.volume += candle.volume
        self.trades += candle.trades
        self.tick_count += 1

    def to_candle(self, symbol: str, exchange: str, timeframe: Timeframe) -> Candle:
        """Convert builder to frozen Candle."""
        return Candle(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            timestamp=self.timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            trades=self.trades,
        )


@dataclass(slots=True)
class _AggregationState:
    """State for a single (symbol, exchange, timeframe) aggregation."""
    current_builder: _CandleBuilder | None = None
    last_timestamp: datetime | None = None
    period_seconds: int = 60
    timeframe: Timeframe = Timeframe.ONE_MINUTE

    def period_for(self, timestamp: datetime) -> datetime:
        """Get the start of the aggregation period for a timestamp."""
        timestamp = timestamp.astimezone(UTC)
        if self.timeframe == Timeframe.ONE_MONTH:
            return timestamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if self.timeframe == Timeframe.ONE_WEEK:
            day_start = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            return day_start - timedelta(days=day_start.weekday())
        if self.timeframe == Timeframe.ONE_DAY:
            return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        period_start_seconds = int(timestamp.timestamp()) // self.period_seconds * self.period_seconds
        return datetime.fromtimestamp(period_start_seconds, tz=UTC)

    def start_new_period(self, timestamp: datetime) -> None:
        """Start a new aggregation period."""
        period_start = self.period_for(timestamp)
        self.current_builder = _CandleBuilder(timestamp=period_start)
        self.last_timestamp = timestamp

    def needs_new_period(self, timestamp: datetime) -> bool:
        """Check if timestamp belongs to a new aggregation period."""
        if self.current_builder is None:
            return True
        return self.period_for(timestamp) > self.period_for(self.current_builder.timestamp)

    def finalize(self, key: AggregationKey) -> Candle | None:
        """Finalize the current builder if it has data."""
        if self.current_builder is not None and self.current_builder.tick_count > 0:
            candle = self.current_builder.to_candle(
                symbol=key.symbol,
                exchange=key.exchange,
                timeframe=key.timeframe,
            )
            self.current_builder = None
            return candle
        return None


class TimeframeAggregationEngine:
    """Infrastructure service that aggregates lower timeframe data into higher timeframe candles.

    This is NOT a plugin. It is shared infrastructure exposed through DI.
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        event_bus: AsyncEventBus,
        supported_timeframes: Sequence[Timeframe] | None = None,
    ) -> None:
        self._repo = repository
        self._bus = event_bus
        self._supported = supported_timeframes or list(AGGREGATION_TIMEFRAMES)
        self._states: dict[AggregationKey, _AggregationState] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._subscription_tick: Subscription | None = None
        self._subscription_candle: Subscription | None = None

    @property
    def supported_timeframes(self) -> list[Timeframe]:
        return list(self._supported)

    async def start(self) -> None:
        """Start the aggregation engine and subscribe to events."""
        if self._running:
            return

        self._running = True

        async def on_tick(event: EventEnvelope) -> None:
            await self._handle_tick(event)

        async def on_candle(event: EventEnvelope) -> None:
            await self._handle_candle(event)

        self._subscription_tick = await self._bus.subscribe(
            TICK_RECEIVED,
            on_tick,
        )
        self._subscription_candle = await self._bus.subscribe(
            CANDLE_CLOSED,
            on_candle,
        )
        logger.info("Timeframe aggregation engine started.")

    async def stop(self) -> None:
        """Stop the aggregation engine."""
        if not self._running:
            return

        self._running = False
        if self._subscription_tick:
            await self._bus.unsubscribe(self._subscription_tick)
        if self._subscription_candle:
            await self._bus.unsubscribe(self._subscription_candle)
        self._subscription_tick = None
        self._subscription_candle = None
        logger.info("Timeframe aggregation engine stopped.")

    async def _handle_tick(self, event: EventEnvelope) -> None:
        """Handle a tick received event."""
        payload = event.payload
        venue = _required_text(payload, "venue")
        tick = Tick(
            symbol=_required_text(payload, "symbol"),
            exchange=venue,
            timestamp=_required_timestamp(payload, "received_at"),
            price=_required_number(payload, "price"),
            size=_required_number(payload, "size"),
            side=_optional_text(payload, "side", default="unknown"),
        )
        await self._repo.append_tick(tick)
        await self._process_tick(tick)

    async def _handle_candle(self, event: EventEnvelope) -> None:
        """Handle a lower timeframe candle closed event."""
        payload = event.payload
        candle = Candle(
            symbol=_required_text(payload, "symbol"),
            exchange=_required_text(payload, "venue"),
            timeframe=Timeframe(_required_text(payload, "timeframe")),
            timestamp=_required_timestamp(payload, "opened_at"),
            open=_required_number(payload, "open_price"),
            high=_required_number(payload, "high_price"),
            low=_required_number(payload, "low_price"),
            close=_required_number(payload, "close_price"),
            volume=_required_number(payload, "volume"),
        )
        await self._repo.append_candle(candle)
        await self._process_candle(candle)

    async def _process_tick(self, tick: Tick) -> None:
        """Process a tick for all supported aggregation timeframes."""
        async with self._lock:
            for timeframe in self._supported:
                key = AggregationKey(tick.symbol, tick.exchange, timeframe)
                state = self._get_state(key)
                if state.current_builder is not None and state.period_for(tick.timestamp) < state.current_builder.timestamp:
                    logger.warning("Ignoring out-of-order tick for %s:%s at %s", tick.symbol, tick.exchange, tick.timestamp)
                    continue
                if state.needs_new_period(tick.timestamp):
                    finalized = state.finalize(key)
                    if finalized is not None:
                        await self._publish_candle(key, finalized)
                    state.start_new_period(tick.timestamp)
                if state.current_builder is not None:
                    state.current_builder.apply_tick(tick)
                    state.last_timestamp = tick.timestamp

    async def _process_candle(self, candle: Candle) -> None:
        """Process a closed lower timeframe candle for all supported aggregation timeframes."""
        async with self._lock:
            for timeframe in self._supported:
                if not self._is_base_timeframe(candle.timeframe, timeframe):
                    continue
                key = AggregationKey(candle.symbol, candle.exchange, timeframe)
                state = self._get_state(key)
                if state.current_builder is not None and state.period_for(candle.timestamp) < state.current_builder.timestamp:
                    logger.warning("Ignoring out-of-order candle for %s:%s at %s", candle.symbol, candle.exchange, candle.timestamp)
                    continue
                if state.needs_new_period(candle.timestamp):
                    finalized = state.finalize(key)
                    if finalized is not None:
                        await self._publish_candle(key, finalized)
                    state.start_new_period(candle.timestamp)

                if state.current_builder is not None:
                    state.current_builder.apply_candle(candle)
                    state.last_timestamp = candle.timestamp

    def _get_state(self, key: AggregationKey) -> _AggregationState:
        """Get or create aggregation state for a key."""
        if key not in self._states:
            period_seconds = _TIMEFRAME_SECONDS.get(key.timeframe, 60)
            self._states[key] = _AggregationState(
                period_seconds=period_seconds,
                timeframe=key.timeframe,
            )
        return self._states[key]

    async def _publish_candle(self, key: AggregationKey, candle: Candle) -> None:
        """Publish a completed aggregated candle."""
        closed_at = candle.timestamp + timedelta(seconds=_TIMEFRAME_SECONDS[key.timeframe])
        payload = CandleClosedPayload(
            symbol=key.symbol,
            timeframe=key.timeframe.value,
            opened_at=candle.timestamp,
            closed_at=closed_at,
            open_price=candle.open,
            high_price=candle.high,
            low_price=candle.low,
            close_price=candle.close,
            volume=candle.volume,
            venue=key.exchange,
        ).to_payload()
        await self._repo.append_candle(candle)
        await self._bus.publish(
            EventEnvelope.create(
                source="timeframe_aggregator",
                event_type=TIMEFRAME_CANDLE_CLOSED,
                payload=payload,
                timestamp=closed_at,
            )
        )
        logger.debug(
            "Published %s candle for %s:%s @ %s",
            key.timeframe,
            key.symbol,
            key.exchange,
            candle.timestamp,
        )

    def _is_base_timeframe(self, source: Timeframe, target: Timeframe) -> bool:
        """Check if source timeframe is the base unit for target aggregation."""
        if source == Timeframe.TICK:
            return True
        source_secs = _TIMEFRAME_SECONDS.get(source, 0)
        target_secs = _TIMEFRAME_SECONDS.get(target, 0)
        if source_secs == 0 or target_secs == 0:
            return False
        return target_secs > source_secs and target_secs % source_secs == 0


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _optional_text(payload: Mapping[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else default


def _required_number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric.")
    return float(value)


def _required_timestamp(payload: Mapping[str, Any], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a timezone-aware ISO timestamp.")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"{key} must be timezone-aware.")
    return timestamp.astimezone(UTC)


# Mapping of timeframes to seconds
_TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.TICK: 0,
    Timeframe.ONE_SECOND: 1,
    Timeframe.FIVE_SECONDS: 5,
    Timeframe.ONE_MINUTE: 60,
    Timeframe.THREE_MINUTES: 180,
    Timeframe.FIVE_MINUTES: 300,
    Timeframe.TEN_MINUTES: 600,
    Timeframe.FIFTEEN_MINUTES: 900,
    Timeframe.THIRTY_MINUTES: 1800,
    Timeframe.ONE_HOUR: 3600,
    Timeframe.TWO_HOURS: 7200,
    Timeframe.FOUR_HOURS: 14400,
    Timeframe.ONE_DAY: 86400,
    Timeframe.ONE_WEEK: 604800,
    Timeframe.ONE_MONTH: 2592000,
}
