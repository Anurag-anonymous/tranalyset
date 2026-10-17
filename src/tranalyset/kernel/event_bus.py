from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import os
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import IntEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import EventHandler


def _default_worker_count() -> int:
    return max(1, min(4, os.cpu_count() or 1))


EventBusErrorHandler = Callable[[EventEnvelope, EventHandler, BaseException], Awaitable[None]]
EventFilter = Callable[[EventEnvelope], bool | Awaitable[bool]]


class EventBusNotRunningError(RuntimeError):
    pass


class DeadLetterSeverity(IntEnum):
    ERROR = 0
    CRITICAL = 1


class EventPriority(IntEnum):
    CRITICAL = 0
    HIGH = 25
    NORMAL = 50
    LOW = 75
    BACKGROUND = 100


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    initial_delay_seconds: float = 0.0
    backoff_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "Retry policy requires at least one attempt."
            raise ValueError(msg)
        if self.initial_delay_seconds < 0:
            msg = "Retry delay must not be negative."
            raise ValueError(msg)
        if self.backoff_multiplier < 1:
            msg = "Retry backoff multiplier must be greater than or equal to 1."
            raise ValueError(msg)

    def delay_for_attempt(self, attempt_number: int) -> float:
        if attempt_number <= 1:
            return 0.0
        return self.initial_delay_seconds * (
            self.backoff_multiplier ** max(attempt_number - 2, 0)
        )


@dataclass(frozen=True, slots=True)
class Subscription:
    topic_pattern: str
    handler: EventHandler
    event_filter: EventFilter | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    subscription_id: str = field(default_factory=lambda: str(uuid4()))
    _pattern_parts: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_pattern_parts", tuple(self.topic_pattern.split(".")))

    @property
    def event_type(self) -> EventType:
        return EventType(self.topic_pattern)


@dataclass(frozen=True, slots=True)
class DeadLetterEvent:
    dead_letter_id: str
    event: EventEnvelope
    subscription: Subscription
    error_type: str
    error_message: str
    attempts: int
    failed_at: datetime
    severity: DeadLetterSeverity
    redriven_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EventBusMetrics:
    events_published: int = 0
    events_dispatched: int = 0
    handler_successes: int = 0
    handler_failures: int = 0
    handler_retries: int = 0
    events_filtered: int = 0
    filter_failures: int = 0
    dead_lettered: int = 0
    critical_dead_lettered: int = 0
    dead_letter_queue_depth: int = 0
    dead_letter_redriven: int = 0


@dataclass(order=True, slots=True)
class _QueuedEvent:
    priority: int
    sequence: int
    event: EventEnvelope = field(compare=False)
    subscription: Subscription | None = field(default=None, compare=False)


@dataclass(slots=True)
class _MutableMetrics:
    events_published: int = 0
    events_dispatched: int = 0
    handler_successes: int = 0
    handler_failures: int = 0
    handler_retries: int = 0
    events_filtered: int = 0
    filter_failures: int = 0
    dead_lettered: int = 0
    critical_dead_lettered: int = 0
    dead_letter_queue_depth: int = 0
    dead_letter_redriven: int = 0

    def snapshot(self) -> EventBusMetrics:
        return EventBusMetrics(
            events_published=self.events_published,
            events_dispatched=self.events_dispatched,
            handler_successes=self.handler_successes,
            handler_failures=self.handler_failures,
            handler_retries=self.handler_retries,
            events_filtered=self.events_filtered,
            filter_failures=self.filter_failures,
            dead_lettered=self.dead_lettered,
            critical_dead_lettered=self.critical_dead_lettered,
            dead_letter_queue_depth=self.dead_letter_queue_depth,
            dead_letter_redriven=self.dead_letter_redriven,
        )


async def _log_event_handler_error(
    event: EventEnvelope,
    handler: EventHandler,
    error: BaseException,
) -> None:
    logger = logging.getLogger("tranalyset.event_bus")
    logger.exception(
        "Event handler failed after retries for event_type=%s source=%s handler=%r",
        event.event_type,
        event.source,
        handler,
        exc_info=error,
    )


def _is_async_callable(handler: EventHandler) -> bool:
    return inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
        getattr(handler, "__call__", None)
    )


def _has_topic_wildcard(topic_pattern: str) -> bool:
    return "*" in topic_pattern or "#" in topic_pattern


def _topic_matches(topic_pattern: str, topic: str) -> bool:
    pattern_parts = topic_pattern.split(".")
    topic_parts = topic.split(".")
    return _topic_parts_match(pattern_parts, topic_parts)


def _topic_matches_pre_split(pattern_parts: tuple[str, ...], topic: str) -> bool:
    topic_parts = topic.split(".")
    pattern_index = 0
    topic_index = 0
    while pattern_index < len(pattern_parts):
        pattern = pattern_parts[pattern_index]
        if pattern == "#":
            return True
        if topic_index >= len(topic_parts):
            return False
        if pattern != "*" and pattern != topic_parts[topic_index]:
            return False
        pattern_index += 1
        topic_index += 1
    return topic_index == len(topic_parts)


def _topic_parts_match(pattern_parts: list[str], topic_parts: list[str]) -> bool:
    pattern_index = 0
    topic_index = 0

    while pattern_index < len(pattern_parts):
        pattern = pattern_parts[pattern_index]
        if pattern == "#":
            return True
        if topic_index >= len(topic_parts):
            return False
        if pattern != "*" and pattern != topic_parts[topic_index]:
            return False
        pattern_index += 1
        topic_index += 1

    return topic_index == len(topic_parts)


def _handler_name(handler: EventHandler) -> str:
    return getattr(handler, "__qualname__", repr(handler))


def _dead_letter_severity(event: EventEnvelope) -> DeadLetterSeverity:
    topic = "".join(character.lower() for character in str(event.event_type) if character.isalnum())
    critical_topics = {
        "tickreceived",
        "candleclosed",
        "timeframecandleclosed",
        "marketopened",
        "marketclosed",
        "connectionlost",
        "connectionrestored",
        "traderequested",
        "tradeupdated",
        "portfolioupdated",
        "riskapproved",
        "riskrejected",
        "positionsizecalculated",
        "stoplossupdated",
        "takeprofitupdated",
        "trailingstopupdated",
    }
    if topic in critical_topics or topic.startswith(("marketdata", "risk")):
        return DeadLetterSeverity.CRITICAL
    return DeadLetterSeverity.ERROR


class AsyncEventBus:
    def __init__(
        self,
        *,
        worker_count: int | None = None,
        max_queue_size: int = 0,
        dead_letter_limit: int = 1_000,
        error_handler: EventBusErrorHandler = _log_event_handler_error,
        logger: logging.Logger | None = None,
    ) -> None:
        if worker_count is not None and worker_count < 1:
            msg = "Event bus requires at least one worker."
            raise ValueError(msg)
        if worker_count is None:
            worker_count = _default_worker_count()
        if max_queue_size < 0:
            msg = "Event bus queue size must not be negative."
            raise ValueError(msg)
        if dead_letter_limit < 1:
            msg = "Dead letter queue limit must be positive."
            raise ValueError(msg)

        self._worker_count = worker_count
        self._error_handler = error_handler
        self._logger = logger or logging.getLogger("tranalyset.event_bus")
        self._completion_queue: asyncio.Queue[None] = asyncio.Queue()
        self._capacity = asyncio.Semaphore(max_queue_size) if max_queue_size else None
        self._exact_subscriptions: dict[EventType, tuple[Subscription, ...]] = {}
        self._topic_subscriptions: tuple[Subscription, ...] = ()
        self._subscription_lock = asyncio.Lock()
        self._redrive_lock = asyncio.Lock()
        self._workers: list[asyncio.Task[None]] = []
        self._ready_streams: asyncio.PriorityQueue[
            tuple[int, int, tuple[str, object]]
        ] = asyncio.PriorityQueue()
        self._stream_queues: dict[tuple[str, object], deque[_QueuedEvent]] = {}
        self._scheduled_streams: set[tuple[str, object]] = set()
        self._sequence = itertools.count()
        self._dead_letters: deque[DeadLetterEvent] = deque(maxlen=dead_letter_limit)
        self._metrics = _MutableMetrics()
        self._running = False
        self._subscription_cache: dict[EventType, tuple[Subscription, ...]] = {}
        self._cache_dirty: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(), name=f"tranalyset-event-bus-{index}")
            for index in range(self._worker_count)
        ]
        self._logger.info("Event bus started with %s workers.", self._worker_count)

    async def stop(self) -> None:
        if not self._running:
            return
        await self._completion_queue.join()
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._logger.info("Event bus stopped.")

    async def publish(
        self,
        event: EventEnvelope,
        *,
        priority: EventPriority | int = EventPriority.NORMAL,
    ) -> None:
        if not self._running:
            msg = "Cannot publish events before the event bus is running."
            raise EventBusNotRunningError(msg)
        await self._enqueue_event(event, priority=priority)

    async def publish_many(
        self,
        events: Sequence[EventEnvelope],
        *,
        priority: EventPriority | int = EventPriority.NORMAL,
    ) -> None:
        for event in events:
            await self.publish(event, priority=priority)

    async def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
        *,
        event_filter: EventFilter | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> Subscription:
        return await self.subscribe_topic(
            str(event_type),
            handler,
            event_filter=event_filter,
            retry_policy=retry_policy,
        )

    async def subscribe_topic(
        self,
        topic_pattern: str,
        handler: EventHandler,
        *,
        event_filter: EventFilter | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> Subscription:
        if not topic_pattern:
            msg = "Subscription topic pattern must not be empty."
            raise ValueError(msg)
        if not _is_async_callable(handler):
            msg = "Event handlers must be async callables."
            raise TypeError(msg)

        subscription = Subscription(
            topic_pattern=topic_pattern,
            handler=handler,
            event_filter=event_filter,
            retry_policy=retry_policy or RetryPolicy(),
        )

        async with self._subscription_lock:
            if _has_topic_wildcard(topic_pattern):
                existing_topic = next(
                    (
                        item
                        for item in self._topic_subscriptions
                        if item.topic_pattern == topic_pattern and item.handler == handler
                    ),
                    None,
                )
                if existing_topic is not None:
                    return existing_topic
                self._topic_subscriptions = self._topic_subscriptions + (subscription,)
            else:
                event_type = EventType(topic_pattern)
                existing = self._exact_subscriptions.get(event_type, ())
                existing_exact = next((item for item in existing if item.handler == handler), None)
                if existing_exact is not None:
                    return existing_exact
                self._exact_subscriptions[event_type] = existing + (subscription,)

        self._mark_cache_dirty()
        self._logger.debug(
            "Event subscription registered topic_pattern=%s handler=%s.",
            topic_pattern,
            _handler_name(handler),
        )
        return subscription

    async def unsubscribe(self, subscription: Subscription) -> None:
        async with self._subscription_lock:
            if _has_topic_wildcard(subscription.topic_pattern):
                self._topic_subscriptions = tuple(
                    item
                    for item in self._topic_subscriptions
                    if item.subscription_id != subscription.subscription_id
                )
                self._mark_cache_dirty()
                return

            event_type = EventType(subscription.topic_pattern)
            existing = self._exact_subscriptions.get(event_type, ())
            remaining = tuple(
                item for item in existing if item.subscription_id != subscription.subscription_id
            )
            if remaining:
                self._exact_subscriptions[event_type] = remaining
            else:
                self._exact_subscriptions.pop(event_type, None)
            self._mark_cache_dirty()

    async def wait_until_idle(self) -> None:
        await self._completion_queue.join()

    def metrics(self) -> EventBusMetrics:
        return self._metrics.snapshot()

    def dead_letters(self) -> tuple[DeadLetterEvent, ...]:
        return tuple(self._dead_letters)

    async def redrive_dead_letter(self, dead_letter_id: str) -> None:
        if not self._running:
            raise EventBusNotRunningError("Cannot redrive events while the event bus is stopped.")
        async with self._redrive_lock:
            dead_letter = next(
                (
                    item
                    for item in self._dead_letters
                    if item.dead_letter_id == dead_letter_id
                ),
                None,
            )
            if dead_letter is None:
                raise KeyError(f"Dead letter {dead_letter_id!r} is not retained.")
            if dead_letter.redriven_at is not None:
                raise ValueError(f"Dead letter {dead_letter_id!r} was already redriven.")
            await self._enqueue_event(
                dead_letter.event,
                priority=EventPriority.HIGH,
                subscription=dead_letter.subscription,
            )
            for index, retained in enumerate(self._dead_letters):
                if retained.dead_letter_id == dead_letter_id:
                    self._dead_letters[index] = replace(
                        retained,
                        redriven_at=datetime.now(UTC),
                    )
                    break
            self._metrics.dead_letter_redriven += 1
            self._logger.info(
                "Dead letter queued for controlled redrive id=%s event_type=%s handler=%s.",
                dead_letter_id,
                dead_letter.event.event_type,
                _handler_name(dead_letter.subscription.handler),
            )

    def subscription_snapshot(self) -> Mapping[str, tuple[Subscription, ...]]:
        snapshot = {
            str(event_type): subscriptions
            for event_type, subscriptions in self._exact_subscriptions.items()
        }
        snapshot["<topic-patterns>"] = self._topic_subscriptions
        return MappingProxyType(snapshot)

    async def _enqueue_event(
        self,
        event: EventEnvelope,
        *,
        priority: EventPriority | int,
        subscription: Subscription | None = None,
    ) -> None:
        if not self._running:
            raise EventBusNotRunningError("Cannot enqueue events while the event bus is stopped.")
        if self._capacity is not None:
            await self._capacity.acquire()
            if not self._running:
                self._capacity.release()
                raise EventBusNotRunningError(
                    "Cannot enqueue events while the event bus is stopping."
                )
        sequence = next(self._sequence)
        queued_event = _QueuedEvent(
            priority=int(priority),
            sequence=sequence,
            event=event,
            subscription=subscription,
        )
        stream_key = self._stream_key(queued_event)
        stream = self._stream_queues.setdefault(stream_key, deque())
        stream.append(queued_event)
        self._completion_queue.put_nowait(None)
        if stream_key not in self._scheduled_streams:
            self._scheduled_streams.add(stream_key)
            self._ready_streams.put_nowait((queued_event.priority, sequence, stream_key))
        self._metrics.events_published += 1
        self._logger.debug(
            "Event published event_type=%s source=%s priority=%s.",
            event.event_type,
            event.source,
            queued_event.priority,
        )

    async def _worker(self) -> None:
        while True:
            _, _, stream_key = await self._ready_streams.get()
            queued_event: _QueuedEvent | None = None
            try:
                stream = self._stream_queues[stream_key]
                queued_event = stream.popleft()
                if self._capacity is not None:
                    self._capacity.release()
                await self._dispatch(queued_event.event, queued_event.subscription)
            finally:
                self._ready_streams.task_done()
                if queued_event is not None:
                    self._completion_queue.task_done()
                stream = self._stream_queues.get(stream_key)
                if stream:
                    next_event = stream[0]
                    self._ready_streams.put_nowait(
                        (next_event.priority, next_event.sequence, stream_key)
                    )
                else:
                    self._stream_queues.pop(stream_key, None)
                    self._scheduled_streams.discard(stream_key)

    def _stream_key(self, queued_event: _QueuedEvent) -> tuple[str, object]:
        payload = queued_event.event.payload
        for field_name in ("trade_id", "portfolio_id", "position_id", "order_id"):
            value = payload.get(field_name)
            if value is not None:
                return field_name, str(value)

        symbol = payload.get("symbol", payload.get("instrument", payload.get("asset")))
        if symbol is not None:
            exchange = payload.get("exchange", payload.get("venue", ""))
            timeframe = payload.get("timeframe", "")
            return "market", (str(exchange), str(symbol), str(timeframe))

        return "event", queued_event.sequence

    async def _dispatch(
        self,
        event: EventEnvelope,
        only_subscription: Subscription | None = None,
    ) -> None:
        subscriptions = (
            (only_subscription,)
            if only_subscription is not None
            else self._subscriptions_for(event.event_type)
        )
        for subscription in subscriptions:
            self._metrics.events_dispatched += 1
            if not await self._passes_filter(subscription, event):
                continue
            await self._deliver(subscription, event)

    async def _passes_filter(self, subscription: Subscription, event: EventEnvelope) -> bool:
        if subscription.event_filter is None:
            return True

        try:
            result = subscription.event_filter(event)
            if inspect.isawaitable(result):
                result = await result
            if not bool(result):
                self._metrics.events_filtered += 1
                return False
            return True
        except Exception as error:
            self._metrics.filter_failures += 1
            await self._dead_letter(subscription, event, error, attempts=0)
            return False

    async def _deliver(self, subscription: Subscription, event: EventEnvelope) -> None:
        last_error: BaseException | None = None
        for attempt in range(1, subscription.retry_policy.max_attempts + 1):
            delay = subscription.retry_policy.delay_for_attempt(attempt)
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await subscription.handler(event)
                self._metrics.handler_successes += 1
                return
            except Exception as error:
                last_error = error
                self._metrics.handler_failures += 1
                if attempt < subscription.retry_policy.max_attempts:
                    self._metrics.handler_retries += 1
                    is_final = (attempt + 1) >= subscription.retry_policy.max_attempts
                    exc = (type(error), error, error.__traceback__) if is_final else None
                    self._logger.warning(
                        "Event handler failed; retrying event_type=%s source=%s handler=%s "
                        "attempt=%s max_attempts=%s.",
                        event.event_type,
                        event.source,
                        _handler_name(subscription.handler),
                        attempt,
                        subscription.retry_policy.max_attempts,
                        exc_info=exc,
                    )

        if last_error is not None:
            await self._dead_letter(
                subscription,
                event,
                last_error,
                attempts=subscription.retry_policy.max_attempts,
            )
            await self._error_handler(event, subscription.handler, last_error)

    async def _dead_letter(
        self,
        subscription: Subscription,
        event: EventEnvelope,
        error: BaseException,
        *,
        attempts: int,
    ) -> None:
        self._metrics.dead_lettered += 1
        dead_letter = DeadLetterEvent(
            dead_letter_id=str(uuid4()),
            event=event,
            subscription=subscription,
            error_type=type(error).__name__,
            error_message=str(error),
            attempts=attempts,
            failed_at=datetime.now(UTC),
            severity=_dead_letter_severity(event),
        )
        if dead_letter.severity is DeadLetterSeverity.CRITICAL:
            self._metrics.critical_dead_lettered += 1
        self._dead_letters.append(dead_letter)
        self._metrics.dead_letter_queue_depth = len(self._dead_letters)
        logger = (
            self._logger.critical
            if dead_letter.severity is DeadLetterSeverity.CRITICAL
            else self._logger.warning
        )
        logger(
            "Event moved to %s dead letter queue id=%s event_type=%s source=%s handler=%s "
            "error_type=%s attempts=%s.",
            dead_letter.severity.name.lower(),
            dead_letter.dead_letter_id,
            event.event_type,
            event.source,
            _handler_name(subscription.handler),
            dead_letter.error_type,
            attempts,
            exc_info=(type(error), error, error.__traceback__),
        )

    def _mark_cache_dirty(self) -> None:
        self._cache_dirty = True

    def _rebuild_cache(self) -> None:
        if not self._cache_dirty:
            return
        self._subscription_cache.clear()
        all_event_types: set[EventType] = set(self._exact_subscriptions)
        for sub in self._topic_subscriptions:
            all_event_types.add(EventType(sub.topic_pattern))
        for event_type in all_event_types:
            exact = self._exact_subscriptions.get(event_type, ())
            topic = str(event_type)
            topic_matches = tuple(
                subscription
                for subscription in self._topic_subscriptions
                if _topic_matches_pre_split(subscription._pattern_parts, topic)
            )
            self._subscription_cache[event_type] = exact + topic_matches
        self._cache_dirty = False

    def _subscriptions_for(self, event_type: EventType) -> tuple[Subscription, ...]:
        self._rebuild_cache()
        cached = self._subscription_cache.get(event_type)
        if cached is not None:
            return cached
        topic = str(event_type)
        topic_matches = tuple(
            subscription
            for subscription in self._topic_subscriptions
            if _topic_matches_pre_split(subscription._pattern_parts, topic)
        )
        result = self._exact_subscriptions.get(event_type, ()) + topic_matches
        self._subscription_cache[event_type] = result
        return result
