from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.models import Tick
from tranalyset.market_data.replay import HistoricalTradingSessionReplay, PlaybackMode, ReplayEngine, ReplayState
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.market_data.contracts import TICK_RECEIVED


async def _session() -> tuple[HistoricalTradingSessionReplay, MarketDataRepository, AsyncEventBus]:
    bus = AsyncEventBus(worker_count=1)
    await bus.start()
    repository = MarketDataRepository(InMemoryStorageBackend())
    replay = HistoricalTradingSessionReplay(ReplayEngine(repository, bus), bus)
    return replay, repository, bus


async def _run_fixture() -> tuple[tuple[str, str, dict[str, object]], ...]:
    replay, repository, bus = await _session()
    start = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
    for index, price in enumerate((100.0, 101.0, 99.0)):
        await repository.append_tick(
            Tick("AAPL", "NASDAQ", start + timedelta(seconds=index), price, 1.0)
        )
    received: list[EventEnvelope] = []

    async def quant_component(event: EventEnvelope) -> None:
        await bus.publish(
            EventEnvelope.create(
                source="quant_component",
                event_type=EventType("FeaturesUpdated"),
                payload={"observed_at": event.timestamp.isoformat(), "symbol": "AAPL", "value": event.payload["price"]},
                timestamp=event.timestamp,
            )
        )

    async def collect(event: EventEnvelope) -> None:
        received.append(event)

    await bus.subscribe(TICK_RECEIVED, quant_component)
    await bus.subscribe_topic("#", collect)
    await replay.load_session("AAPL", "NASDAQ", start, start + timedelta(minutes=1))
    replay.playback_mode = PlaybackMode.STEP
    for _ in range(3):
        await replay.step()
    result = replay.result()
    assert replay.state is ReplayState.COMPLETED
    assert [record.event.timestamp for record in result.events] == sorted(record.event.timestamp for record in result.events)
    assert len(result.quantitative_state_changes) == 3
    outputs = tuple(
        (str(event.event_type), event.timestamp.isoformat(), dict(event.payload))
        for event in received
        if str(event.event_type) == "FeaturesUpdated"
    )
    await replay.stop()
    await bus.stop()
    return outputs


@pytest.mark.asyncio
async def test_historical_session_replay_drives_the_normal_event_bus_pipeline() -> None:
    outputs = await _run_fixture()

    assert [output[2]["value"] for output in outputs] == [100.0, 101.0, 99.0]
    assert [output[1] for output in outputs] == sorted(output[1] for output in outputs)


@pytest.mark.asyncio
async def test_historical_session_replay_is_deterministic_for_identical_data() -> None:
    assert await _run_fixture() == await _run_fixture()
