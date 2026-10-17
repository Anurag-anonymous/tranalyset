"""Benchmark comparisons for the Feature Engineering Engine.

Run with: python -m pytest tests/benchmarks/ -v --tb=short -s
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.feature_engine import FeatureEngineeringService, FeatureEngineeringSettings, FeatureObservation
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED


@pytest.mark.asyncio
async def test_benchmark_feature_generation_latency() -> None:
    backend = InMemoryStorageBackend()
    repo = MarketDataRepository(backend)
    bus = AsyncEventBus()
    await bus.start()
    indicator_cache = IndicatorCache(repository=repo, event_bus=bus)
    aggregation = TimeframeAggregationEngine(repository=repo, event_bus=bus)
    service = FeatureEngineeringService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
        settings=FeatureEngineeringSettings(),
    )

    for offset in range(40):
        ts = datetime.fromtimestamp(time.time(), tz=UTC)
        await repo.append_candle(
            Candle(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=ts,
                open=100.0 + offset * 0.1,
                high=100.5 + offset * 0.1,
                low=99.5 + offset * 0.1,
                close=100.2 + offset * 0.1,
                volume=1_000.0 + (offset * 25.0),
                trades=10 + offset,
            )
        )

    current_time = datetime.now(UTC)
    observation = FeatureObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        observed_at=current_time,
        source_event_type=str(CANDLE_CLOSED),
        open_price=105.0,
        high_price=106.0,
        low_price=104.0,
        close_price=105.5,
        volume=1_600.0,
    )

    # Warm up
    await service.generate(observation)

    count = 2_000
    start = time.perf_counter()
    for _ in range(count):
        await service.generate(observation)
    elapsed = time.perf_counter() - start

    features_per_second = count / elapsed if elapsed > 0 else float("inf")
    print(f"\n  Feature generation latency: {elapsed / count * 1_000:.4f} ms/call")
    print(f"  Feature generation throughput: {features_per_second:,.0f} calls/sec")

    assert await service.latest(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        source_event_type=str(CANDLE_CLOSED),
    ) is not None

    await bus.stop()
