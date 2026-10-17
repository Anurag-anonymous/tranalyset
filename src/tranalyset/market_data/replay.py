"""Replay Engine for historical market session playback through the Event Bus."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus, Subscription
from tranalyset.market_data.ingestion import MarketDataIngestionService
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.market_data.contracts import (
    CANDLE_CLOSED,
    TICK_RECEIVED,
    CandleClosedPayload,
    TickReceivedPayload,
)

logger = logging.getLogger("tranalyset.market_data.replay")


class PlaybackMode(Enum):
    """Replay playback speed modes."""
    REALTIME = "realtime"
    SPEED_2X = "2x"
    SPEED_5X = "5x"
    SPEED_10X = "10x"
    SPEED_100X = "100x"
    STEP = "step"


class ReplayState(Enum):
    """Replay engine state."""
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    SEEKING = "seeking"
    COMPLETED = "completed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class RecordedReplayEvent:
    sequence: int
    event: EventEnvelope


@dataclass(frozen=True, slots=True)
class HistoricalReplayResult:
    events: tuple[RecordedReplayEvent, ...]
    quantitative_state_changes: tuple[RecordedReplayEvent, ...]
    candidates: tuple[RecordedReplayEvent, ...]
    risk_decisions: tuple[RecordedReplayEvent, ...]


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """A single event to replay."""
    timestamp: datetime
    event_type: EventType
    payload: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ReplayEngine:
    """Infrastructure service for replaying historical market data through the Event Bus.

    This is NOT a plugin. It is shared infrastructure exposed through DI.
    """

    def __init__(
        self,
        repository: MarketDataRepository,
        event_bus: AsyncEventBus,
        default_playback_mode: PlaybackMode = PlaybackMode.REALTIME,
    ) -> None:
        self._repo = repository
        self._ingestion = MarketDataIngestionService(repository)
        self._bus = event_bus
        self._default_mode = default_playback_mode
        self._events: list[ReplayEvent] = []
        self._state = ReplayState.IDLE
        self._lock = asyncio.Lock()
        self._current_index = 0
        self._start_time: datetime | None = None
        self._replay_time: datetime | None = None
        self._replay_start: datetime | None = None
        self._playback_task: asyncio.Task | None = None
        self._pause_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._seek_target: int | None = None

    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def playback_mode(self) -> PlaybackMode:
        return self._default_mode

    @property
    def replay_time(self) -> datetime | None:
        """Current deterministic simulated market time, if replay is active."""
        return self._replay_time

    @playback_mode.setter
    def playback_mode(self, value: PlaybackMode) -> None:
        self._default_mode = value

    def _speed_multiplier(self) -> float:
        """Get the speed multiplier for the current playback mode."""
        return {
            PlaybackMode.REALTIME: 1.0,
            PlaybackMode.SPEED_2X: 2.0,
            PlaybackMode.SPEED_5X: 5.0,
            PlaybackMode.SPEED_10X: 10.0,
            PlaybackMode.SPEED_100X: 100.0,
            PlaybackMode.STEP: 0.0,
        }[self._default_mode]

    async def load_session(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
        timeframe: Timeframe | None = None,
    ) -> int:
        """Load historical events from the repository for replay.

        Returns the number of events loaded.
        """
        async with self._lock:
            self._events = []
            # A replay has one canonical market-data stream. Replaying both raw
            # ticks and their derived candles double-counts the same market move.
            if timeframe is None:
                ticks = await self._repo.get_ticks(symbol, exchange, start=start, end=end)
                for tick in ticks:
                    self._events.append(
                        ReplayEvent(
                            timestamp=tick.timestamp,
                            event_type=TICK_RECEIVED,
                            payload=TickReceivedPayload(symbol=tick.symbol, price=tick.price, size=tick.size, received_at=tick.timestamp, venue=tick.exchange, side=tick.side).to_payload(),
                        )
                    )
            else:
                candles = await self._repo.get_candles(symbol, exchange, timeframe, start=start, end=end)
                for candle in candles:
                    self._events.append(
                        ReplayEvent(
                            timestamp=_candle_close_time(candle),
                            event_type=CANDLE_CLOSED,
                            payload=CandleClosedPayload(symbol=candle.symbol, timeframe=candle.timeframe.value, opened_at=candle.timestamp, closed_at=_candle_close_time(candle), open_price=candle.open, high_price=candle.high, low_price=candle.low, close_price=candle.close, volume=candle.volume, venue=candle.exchange).to_payload(),
                        )
                    )
            # Add market open/close events
            self._events.sort(key=lambda e: e.timestamp)
            self._current_index = 0
            self._replay_time = None
            self._state = ReplayState.IDLE
            return len(self._events)

    async def play(self) -> None:
        """Start or resume replay."""
        if self._state in {ReplayState.PLAYING, ReplayState.COMPLETED}:
            return

        async with self._lock:
            if not self._events:
                return
            self._state = ReplayState.PLAYING
            self._stop_event.clear()
            self._pause_event.set()
            self._replay_start = datetime.now(UTC)
            if self._start_time is None:
                self._start_time = self._events[0].timestamp

        self._playback_task = asyncio.create_task(self._run_playback())
        logger.info("Replay started in %s mode.", self._default_mode.value)

    async def pause(self) -> None:
        """Pause replay."""
        if self._state != ReplayState.PLAYING:
            return
        self._pause_event.clear()
        self._state = ReplayState.PAUSED
        logger.info("Replay paused at event %d.", self._current_index)

    async def resume(self) -> None:
        """Resume paused replay."""
        if self._state != ReplayState.PAUSED:
            return
        self._pause_event.set()
        self._state = ReplayState.PLAYING
        logger.info("Replay resumed at event %d.", self._current_index)

    async def stop(self) -> None:
        """Stop replay and reset state."""
        self._stop_event.set()
        self._pause_event.set()
        if self._playback_task:
            await self._playback_task
        async with self._lock:
            self._state = ReplayState.STOPPED
            self._current_index = 0
            self._start_time = None
            self._replay_time = None
            self._replay_start = None
        logger.info("Replay stopped.")

    async def seek(self, event_index: int) -> None:
        """Seek to a specific event index."""
        if not 0 <= event_index < len(self._events):
            raise IndexError(f"Event index {event_index} out of range [0, {len(self._events)}).")
        async with self._lock:
            was_playing = self._state == ReplayState.PLAYING
            self._state = ReplayState.SEEKING
            self._current_index = event_index
            self._seek_target = event_index
            self._replay_start = datetime.now(UTC)
            self._start_time = self._events[event_index].timestamp
            self._state = ReplayState.PLAYING if was_playing else ReplayState.IDLE

    async def step(self) -> None:
        """Publish the next event in step mode."""
        if self._state not in {ReplayState.IDLE, ReplayState.PAUSED, ReplayState.STOPPED}:
            return
        if self._current_index >= len(self._events):
            return
        event = self._events[self._current_index]
        await self._publish_event(event)
        await self._bus.wait_until_idle()
        async with self._lock:
            self._current_index += 1
            if self._current_index >= len(self._events):
                self._state = ReplayState.COMPLETED

    async def progress(self) -> float:
        """Get replay progress as a fraction [0.0, 1.0]."""
        if not self._events:
            return 0.0
        return self._current_index / len(self._events)

    async def _run_playback(self) -> None:
        """Run the playback loop."""
        speed = self._speed_multiplier()
        while self._current_index < len(self._events):
            if self._stop_event.is_set():
                break
            await self._pause_event.wait()
            event = self._events[self._current_index]
            await self._publish_event(event)
            await self._bus.wait_until_idle()
            async with self._lock:
                self._current_index += 1
                if self._current_index >= len(self._events):
                    self._state = ReplayState.COMPLETED
                    break
            if speed > 0:
                next_event = self._events[self._current_index]
                delay = (next_event.timestamp - event.timestamp).total_seconds() / speed
                if delay > 0:
                    try:
                        await asyncio.wait_for(self._pause_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass

    async def _publish_event(self, event: ReplayEvent) -> None:
        """Publish a replay event through the event bus."""
        self._replay_time = event.timestamp.astimezone(UTC)
        envelope = EventEnvelope.create(
            source="replay",
            event_type=event.event_type,
            payload=event.payload,
            timestamp=event.timestamp,
        )
        await self._ingestion.persist_event(envelope)
        await self._bus.publish(envelope)


class HistoricalTradingSessionReplay:
    """Records a historical session while driving the ordinary replay event flow."""

    _STATE_EVENTS = frozenset(
        {
            "FeaturesUpdated",
            "IndicatorsUpdated",
            "PatternDetected",
            "PatternUpdated",
            "MarketStructureUpdated",
            "MarketRegimeChanged",
            "MarketContextUpdated",
            "ProbabilityUpdated",
            "PortfolioUpdated",
        }
    )
    _CANDIDATE_EVENTS = frozenset(
        {"TradeSignalGenerated", "WatchCandidateGenerated", "AcceptedCandidate", "RejectedCandidate"}
    )
    _RISK_EVENTS = frozenset({"RiskApproved", "RiskRejected"})

    def __init__(self, replay_engine: ReplayEngine, event_bus: AsyncEventBus) -> None:
        self._replay_engine = replay_engine
        self._bus = event_bus
        self._subscription: Subscription | None = None
        self._records: list[RecordedReplayEvent] = []
        self._sequence = 0

    @property
    def state(self) -> ReplayState:
        return self._replay_engine.state

    @property
    def playback_mode(self) -> PlaybackMode:
        return self._replay_engine.playback_mode

    @playback_mode.setter
    def playback_mode(self, value: PlaybackMode) -> None:
        self._replay_engine.playback_mode = value

    async def load_session(
        self, symbol: str, exchange: str, start: datetime, end: datetime, timeframe: Timeframe | None = None
    ) -> int:
        self._records.clear()
        self._sequence = 0
        return await self._replay_engine.load_session(symbol, exchange, start, end, timeframe)

    async def start(self) -> None:
        await self._ensure_recording()
        await self._replay_engine.play()

    async def pause(self) -> None:
        await self._replay_engine.pause()

    async def resume(self) -> None:
        await self._replay_engine.resume()

    async def stop(self) -> None:
        await self._replay_engine.stop()
        if self._subscription is not None:
            await self._bus.unsubscribe(self._subscription)
            self._subscription = None

    async def step(self) -> None:
        await self._ensure_recording()
        await self._replay_engine.step()

    async def seek(self, event_index: int) -> None:
        await self._replay_engine.seek(event_index)

    async def wait_until_complete(self) -> None:
        task = self._replay_engine._playback_task
        if task is not None:
            await task
        await self._bus.wait_until_idle()

    def result(self) -> HistoricalReplayResult:
        records = tuple(self._records)
        return HistoricalReplayResult(
            events=records,
            quantitative_state_changes=tuple(record for record in records if str(record.event.event_type) in self._STATE_EVENTS),
            candidates=tuple(record for record in records if str(record.event.event_type) in self._CANDIDATE_EVENTS),
            risk_decisions=tuple(record for record in records if str(record.event.event_type) in self._RISK_EVENTS),
        )

    async def _ensure_recording(self) -> None:
        if self._subscription is not None:
            return

        async def record(event: EventEnvelope) -> None:
            self._sequence += 1
            self._records.append(RecordedReplayEvent(sequence=self._sequence, event=event))

        self._subscription = await self._bus.subscribe_topic("#", record)


def _candle_close_time(candle: Candle) -> datetime:
    seconds = _TIMEFRAME_SECONDS[candle.timeframe]
    return candle.timestamp + timedelta(seconds=seconds)


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
