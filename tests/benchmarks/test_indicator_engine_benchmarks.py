"""Benchmark comparisons for the Indicator Engine.

Run with: python -m pytest tests/benchmarks/ -v --tb=short -s
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.feature_engine import FeatureEngineeringService, FeatureEngineeringSettings, FeatureObservation
from tranalyset.plugins.indicator_engine import IndicatorEngineService, IndicatorEngineSettings, IndicatorObservation
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED


@pytest.mark.asyncio
async def test_benchmark_indicator_generation_latency() -> None:
    backend = InMemoryStorageBackend()
    repo = MarketDataRepository(backend)
    bus = object()
    indicator_cache = IndicatorCache(repository=repo, event_bus=bus)  # type: ignore[arg-type]
    aggregation = TimeframeAggregationEngine(repository=repo, event_bus=bus)  # type: ignore[arg-type]
    feature_engine = FeatureEngineeringService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
        settings=FeatureEngineeringSettings(),
    )
    service = IndicatorEngineService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
        feature_engine=feature_engine,
        settings=IndicatorEngineSettings(),
    )

    for index in range(80):
        timestamp = datetime(2026, 1, 1, 9, 30, tzinfo=UTC) + timedelta(minutes=index)
        await repo.append_candle(
            Candle(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=timestamp,
                open=100.0 + index * 0.25,
                high=100.75 + index * 0.25,
                low=99.5 + index * 0.25,
                close=100.25 + index * 0.25,
                volume=1_000.0 + index * 10.0,
                trades=10 + index,
            )
        )

    feature_observation = FeatureObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        observed_at=datetime(2026, 1, 1, 10, 45, tzinfo=UTC),
        source_event_type=str(CANDLE_CLOSED),
        open_price=120.0,
        high_price=121.0,
        low_price=119.25,
        close_price=120.5,
        volume=1_750.0,
    )
    await feature_engine.generate(feature_observation)

    observation = IndicatorObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        observed_at=datetime(2026, 1, 1, 10, 46, tzinfo=UTC),
        source_event_type=str(CANDLE_CLOSED),
        open_price=120.5,
        high_price=121.5,
        low_price=119.75,
        close_price=121.0,
        volume=1_820.0,
    )

    # Warm up
    await service.ingest(observation)

    count = 1_000
    start = time.perf_counter()
    for _ in range(count):
        await service.ingest(observation)
    elapsed = time.perf_counter() - start

    print(f"\n  Indicator generation latency: {elapsed / count * 1_000:.4f} ms/call")
    print(f"  Indicator generation throughput: {count / elapsed:,.0f} calls/sec")

    assert await service.latest(symbol="AAPL", exchange="NASDAQ", timeframe="1m") is not None
