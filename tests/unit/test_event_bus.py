import asyncio
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import (
    AsyncEventBus,
    DeadLetterSeverity,
    EventPriority,
    RetryPolicy,
)


async def test_event_bus_dispatches_to_subscribers_asynchronously() -> None:
    event_type = EventType("platform.test")
    bus = AsyncEventBus()
    received: list[EventEnvelope] = []

    async def handler(event: EventEnvelope) -> None:
        received.append(event)

    await bus.start()
    await bus.subscribe(event_type, handler)

    event = EventEnvelope(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        source="test",
        event_type=event_type,
    )
    await bus.publish(event)
    await bus.wait_until_idle()
    await bus.stop()

    assert received == [event]


async def test_event_bus_handler_errors_do_not_stop_delivery() -> None:
    event_type = EventType("platform.test")
    errors: list[BaseException] = []
    delivered: list[str] = []

    async def error_handler(
        event: EventEnvelope,
        handler: object,
        error: BaseException,
    ) -> None:
        errors.append(error)

    async def failing_handler(event: EventEnvelope) -> None:
        raise RuntimeError("handler failed")

    async def working_handler(event: EventEnvelope) -> None:
        delivered.append(event.source)

    bus = AsyncEventBus(error_handler=error_handler)
    await bus.start()
    await bus.subscribe(event_type, failing_handler)
    await bus.subscribe(event_type, working_handler)
    await bus.publish(EventEnvelope.create(source="test", event_type=event_type))
    await bus.wait_until_idle()
    await bus.stop()

    assert len(errors) == 1
    assert delivered == ["test"]


async def test_event_bus_routes_wildcard_topics() -> None:
    bus = AsyncEventBus()
    received: list[str] = []

    async def handler(event: EventEnvelope) -> None:
        received.append(str(event.event_type))

    await bus.start()
    await bus.subscribe_topic("market.*", handler)
    await bus.publish(EventEnvelope.create(source="test", event_type=EventType("market.tick")))
    await bus.publish(EventEnvelope.create(source="test", event_type=EventType("risk.alert")))
    await bus.wait_until_idle()
    await bus.stop()

    assert received == ["market.tick"]


async def test_event_bus_filters_events_before_delivery() -> None:
    bus = AsyncEventBus()
    received: list[str] = []

    async def handler(event: EventEnvelope) -> None:
        received.append(str(event.payload["symbol"]))

    def only_aapl(event: EventEnvelope) -> bool:
        return event.payload["symbol"] == "AAPL"

    await bus.start()
    await bus.subscribe(EventType("market.tick"), handler, event_filter=only_aapl)
    await bus.publish(
        EventEnvelope.create(
            source="test",
            event_type=EventType("market.tick"),
            payload={"symbol": "MSFT"},
        )
    )
    await bus.publish(
        EventEnvelope.create(
            source="test",
            event_type=EventType("market.tick"),
            payload={"symbol": "AAPL"},
        )
    )
    await bus.wait_until_idle()
    await bus.stop()

    assert received == ["AAPL"]
    assert bus.metrics().events_filtered == 1


async def test_event_bus_dispatches_higher_priority_events_first() -> None:
    bus = AsyncEventBus()
    received: list[str] = []

    async def handler(event: EventEnvelope) -> None:
        received.append(event.source)

    await bus.start()
    await bus.subscribe(EventType("platform.test"), handler)
    await bus.publish(
        EventEnvelope.create(source="low", event_type=EventType("platform.test")),
        priority=EventPriority.LOW,
    )
    await bus.publish(
        EventEnvelope.create(source="critical", event_type=EventType("platform.test")),
        priority=EventPriority.CRITICAL,
    )
    await bus.publish(
        EventEnvelope.create(source="high", event_type=EventType("platform.test")),
        priority=EventPriority.HIGH,
    )
    await bus.wait_until_idle()
    await bus.stop()

    assert received == ["critical", "high", "low"]


async def test_event_bus_priority_does_not_reorder_events_within_a_market_stream() -> None:
    bus = AsyncEventBus(worker_count=2)
    received: list[str] = []

    async def handler(event: EventEnvelope) -> None:
        received.append(event.source)

    await bus.start()
    await bus.subscribe(EventType("CandleClosed"), handler)
    payload = {"symbol": "AAPL", "exchange": "NASDAQ", "timeframe": "1m"}
    await bus.publish(
        EventEnvelope.create(source="first", event_type=EventType("CandleClosed"), payload=payload),
        priority=EventPriority.LOW,
    )
    await bus.publish(
        EventEnvelope.create(source="second", event_type=EventType("CandleClosed"), payload=payload),
        priority=EventPriority.CRITICAL,
    )
    await bus.wait_until_idle()
    await bus.stop()

    assert received == ["first", "second"]


async def test_event_bus_retries_failed_handlers_until_success() -> None:
    bus = AsyncEventBus()
    attempts = 0

    async def handler(event: EventEnvelope) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient failure")

    await bus.start()
    await bus.subscribe(
        EventType("platform.test"),
        handler,
        retry_policy=RetryPolicy(max_attempts=3),
    )
    await bus.publish(EventEnvelope.create(source="test", event_type=EventType("platform.test")))
    await bus.wait_until_idle()
    await bus.stop()

    metrics = bus.metrics()
    assert attempts == 3
    assert metrics.handler_failures == 2
    assert metrics.handler_retries == 2
    assert metrics.handler_successes == 1
    assert bus.dead_letters() == ()


async def test_event_bus_moves_exhausted_failures_to_dead_letter_queue() -> None:
    errors: list[BaseException] = []

    async def error_handler(
        event: EventEnvelope,
        handler: object,
        error: BaseException,
    ) -> None:
        errors.append(error)

    async def handler(event: EventEnvelope) -> None:
        raise RuntimeError("permanent failure")

    bus = AsyncEventBus(error_handler=error_handler)
    await bus.start()
    await bus.subscribe(
        EventType("platform.test"),
        handler,
        retry_policy=RetryPolicy(max_attempts=2),
    )
    await bus.publish(EventEnvelope.create(source="test", event_type=EventType("platform.test")))
    await bus.wait_until_idle()
    await bus.stop()

    dead_letters = bus.dead_letters()
    metrics = bus.metrics()
    assert len(errors) == 1
    assert len(dead_letters) == 1
    assert dead_letters[0].attempts == 2
    assert dead_letters[0].error_type == "RuntimeError"
    assert metrics.dead_lettered == 1
    assert metrics.handler_failures == 2


async def test_event_bus_preserves_market_stream_order_without_blocking_other_streams() -> None:
    bus = AsyncEventBus(worker_count=2)
    first_attempt_started = asyncio.Event()
    release_first_attempt = asyncio.Event()
    unrelated_completed = asyncio.Event()
    received: list[str] = []
    attempt_count = 0

    async def handler(event: EventEnvelope) -> None:
        nonlocal attempt_count
        symbol = str(event.payload["symbol"])
        if event.source == "first":
            attempt_count += 1
            received.append(f"first-{attempt_count}")
            if attempt_count == 1:
                first_attempt_started.set()
                await release_first_attempt.wait()
                raise RuntimeError("retry this market stream")
        elif event.source == "second":
            received.append("second")
        else:
            received.append("unrelated")
            unrelated_completed.set()

    await bus.start()
    await bus.subscribe(
        EventType("CandleClosed"),
        handler,
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.05),
    )

    def candle(source: str, symbol: str) -> EventEnvelope:
        return EventEnvelope.create(
            source=source,
            event_type=EventType("CandleClosed"),
            payload={"symbol": symbol, "exchange": "X", "timeframe": "1m"},
        )

    await bus.publish(candle("first", "AAPL"))
    await first_attempt_started.wait()
    await bus.publish(candle("second", "AAPL"))
    await bus.publish(candle("unrelated", "MSFT"))
    await asyncio.wait_for(unrelated_completed.wait(), timeout=1)
    assert received == ["first-1", "unrelated"]

    release_first_attempt.set()
    await bus.wait_until_idle()
    await bus.stop()

    assert received == ["first-1", "unrelated", "first-2", "second"]


async def test_event_bus_redrives_only_the_failed_subscription_once() -> None:
    bus = AsyncEventBus(dead_letter_limit=1)
    should_fail = True
    failed_handler_calls = 0
    other_handler_calls = 0

    async def failed_handler(event: EventEnvelope) -> None:
        nonlocal failed_handler_calls
        failed_handler_calls += 1
        if should_fail:
            raise RuntimeError("temporary failure")

    async def other_handler(event: EventEnvelope) -> None:
        nonlocal other_handler_calls
        other_handler_calls += 1

    await bus.start()
    await bus.subscribe(EventType("TradeRequested"), failed_handler)
    await bus.subscribe(EventType("TradeRequested"), other_handler)
    await bus.publish(EventEnvelope.create(source="test", event_type=EventType("TradeRequested")))
    await bus.wait_until_idle()

    dead_letter = bus.dead_letters()[0]
    assert dead_letter.severity is DeadLetterSeverity.CRITICAL
    assert bus.metrics().critical_dead_lettered == 1
    assert bus.metrics().dead_letter_queue_depth == 1
    should_fail = False
    await bus.redrive_dead_letter(dead_letter.dead_letter_id)
    await bus.wait_until_idle()

    retained = bus.dead_letters()[0]
    assert retained.redriven_at is not None
    assert bus.metrics().dead_letter_redriven == 1
    assert failed_handler_calls == 2
    assert other_handler_calls == 1
    with pytest.raises(ValueError, match="already redriven"):
        await bus.redrive_dead_letter(dead_letter.dead_letter_id)
    await bus.stop()


async def test_event_bus_dead_letter_retention_is_bounded() -> None:
    bus = AsyncEventBus(dead_letter_limit=2)

    async def handler(event: EventEnvelope) -> None:
        raise RuntimeError(event.source)

    await bus.start()
    await bus.subscribe(EventType("platform.failure"), handler)
    for source in ("one", "two", "three"):
        await bus.publish(
            EventEnvelope.create(source=source, event_type=EventType("platform.failure"))
        )
    await bus.wait_until_idle()
    await bus.stop()

    assert len(bus.dead_letters()) == 2


async def test_market_data_dead_letters_are_marked_critical() -> None:
    bus = AsyncEventBus()

    async def handler(event: EventEnvelope) -> None:
        raise RuntimeError("critical stream failed")

    await bus.start()
    await bus.subscribe(EventType("CandleClosed"), handler)
    await bus.publish(EventEnvelope.create(source="feed", event_type=EventType("CandleClosed")))
    await bus.wait_until_idle()
    await bus.stop()

    dead_letter = bus.dead_letters()[0]
    assert dead_letter.severity is DeadLetterSeverity.CRITICAL
    assert bus.metrics().critical_dead_lettered == 1


async def test_bounded_event_bus_allows_handlers_to_publish_follow_up_events() -> None:
    bus = AsyncEventBus(worker_count=1, max_queue_size=1)
    received: list[str] = []

    async def handler(event: EventEnvelope) -> None:
        received.append(event.source)
        if event.source == "initial":
            await bus.publish(
                EventEnvelope.create(
                    source="follow-up",
                    event_type=EventType("platform.test"),
                )
            )

    await bus.start()
    await bus.subscribe(EventType("platform.test"), handler)
    await bus.publish(EventEnvelope.create(source="initial", event_type=EventType("platform.test")))
    await asyncio.wait_for(bus.wait_until_idle(), timeout=1)
    await bus.stop()

    assert received == ["initial", "follow-up"]
