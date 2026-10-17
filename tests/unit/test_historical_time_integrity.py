from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Timeframe
from tranalyset.market_data.replay import PlaybackMode, ReplayEngine
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.context_engine.service import ContextEngineService
from tranalyset.plugins.feature_engine.service import (
    FeatureEngineeringService,
    FeatureEngineeringSettings,
    FeatureObservation,
)


async def test_feature_and_indicator_cache_preserve_market_time() -> None:
    timestamp = datetime(2020, 1, 2, 9, 30, tzinfo=UTC)
    repository = MarketDataRepository(InMemoryStorageBackend())
    cache = IndicatorCache(repository, event_bus=None)  # type: ignore[arg-type]
    await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", timestamp, 100.0)
    cached = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA")
    assert cached is not None
    assert cached.timestamp == timestamp
    assert cached.computed_at == timestamp

    service = FeatureEngineeringService(
        repository=repository,
        indicator_cache=cache,
        aggregation_engine=None,  # type: ignore[arg-type]
        settings=FeatureEngineeringSettings(),
    )
    features = await service.generate(
        FeatureObservation(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe=Timeframe.ONE_MINUTE,
            observed_at=timestamp,
            source_event_type="CandleClosed",
            open_price=100.0,
            high_price=101.0,
            low_price=99.0,
            close_price=100.5,
            volume=10.0,
        )
    )
    assert features.observed_at == timestamp
    assert features.generated_at == timestamp


async def test_historical_context_quality_is_relative_to_market_time() -> None:
    service = ContextEngineService()
    timestamp = datetime(2020, 1, 2, 9, 30, tzinfo=UTC)
    snapshots = await service.observe_indicators(
        {
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "timeframe": "1m",
            "observed_at": timestamp.isoformat(),
            "indicators": {"EMA": 101.0, "SMA": 100.0},
            "source_event_type": "IndicatorsUpdated",
        }
    )
    assert snapshots
    assert snapshots[0].observed_at == timestamp
    assert snapshots[0].context_quality > 0.0


async def test_indicator_cache_does_not_retain_future_values_for_earlier_market_time() -> None:
    repository = MarketDataRepository(InMemoryStorageBackend())
    cache = IndicatorCache(repository, event_bus=None)  # type: ignore[arg-type]
    earlier = datetime(2020, 1, 2, 9, 30, tzinfo=UTC)
    later = earlier + timedelta(minutes=1)
    await cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", later, 102.0)

    assert await cache.get(
        "AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", timestamp=earlier
    ) is None


async def test_replay_clock_advances_only_with_historical_event_time() -> None:
    from tranalyset.kernel.event_bus import AsyncEventBus
    from tranalyset.market_data.models import Tick

    repository = MarketDataRepository(InMemoryStorageBackend())
    timestamp = datetime(2020, 1, 2, 9, 30, tzinfo=UTC)
    await repository.append_tick(Tick("AAPL", "NASDAQ", timestamp, 100.0, 1.0))
    bus = AsyncEventBus()
    replay = ReplayEngine(repository, bus)
    await bus.start()
    await replay.load_session("AAPL", "NASDAQ", timestamp, timestamp + timedelta(minutes=1))
    replay.playback_mode = PlaybackMode.STEP
    await replay.step()

    assert replay.replay_time == timestamp
    await bus.stop()
