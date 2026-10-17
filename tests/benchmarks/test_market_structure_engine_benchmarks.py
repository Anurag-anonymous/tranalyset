"""Benchmark comparisons for the Market Structure Engine.

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
from tranalyset.plugins.feature_engine.contracts import FeatureSet
from tranalyset.plugins.indicator_engine.contracts import IndicatorsUpdatedPayload
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.market_structure_engine import MarketStructureService, MarketStructureSettings


@pytest.mark.asyncio
async def test_benchmark_market_structure_evaluation_latency() -> None:
    backend = InMemoryStorageBackend()
    repo = MarketDataRepository(backend)
    bus = object()
    indicator_cache = IndicatorCache(repository=repo, event_bus=bus)  # type: ignore[arg-type]
    aggregation = TimeframeAggregationEngine(repository=repo, event_bus=bus)  # type: ignore[arg-type]
    service = MarketStructureService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
        settings=MarketStructureSettings(lookback_bars=40, swing_window=1),
    )

    for index in range(80):
        timestamp = datetime(2026, 8, 1, 9, 30, tzinfo=UTC) + timedelta(minutes=index)
        await repo.append_candle(
            Candle(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=timestamp,
                open=100.0 + index * 0.25,
                high=100.8 + index * 0.25,
                low=99.4 + index * 0.25,
                close=100.4 + index * 0.25,
                volume=1_000.0 + index * 12.0,
                trades=10 + index,
            )
    )

    await service.update_indicator_context(
        IndicatorsUpdatedPayload(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe="1m",
            observed_at=datetime(2026, 8, 1, 10, 50, tzinfo=UTC),
            indicators={"EMA": 120.5, "ADX": 31.0, "MACD": 1.0},
            source_event_type=str(CANDLE_CLOSED),
            versions={"EMA": "1.0", "ADX": "1.0", "MACD": "1.0"},
        )
    )

    await service.update_feature_context(
        FeatureSet(
            feature_set_id="feature-aapl",
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe="1m",
            observed_at=datetime(2026, 8, 1, 10, 50, tzinfo=UTC),
            source_event_type=str(CANDLE_CLOSED),
            features={"relative_volume": 1.2, "volume_ratio": 1.15},
            metadata={},
            generated_at=datetime(2026, 8, 1, 10, 50, tzinfo=UTC),
        )
    )

    await repo.append_candle(
        Candle(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe=Timeframe.ONE_MINUTE,
            timestamp=datetime(2026, 8, 1, 10, 51, tzinfo=UTC),
            open=120.5,
            high=121.8,
            low=120.1,
            close=121.4,
            volume=1_750.0,
            trades=42,
        )
    )

    # Warm up
    await service.ingest_snapshot(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        source_event_type=str(CANDLE_CLOSED),
    )

    count = 1_000
    start = time.perf_counter()
    for _ in range(count):
        await service.ingest_snapshot(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe=Timeframe.ONE_MINUTE,
            source_event_type=str(CANDLE_CLOSED),
        )
    elapsed = time.perf_counter() - start

    print(f"\n  Market structure evaluation latency: {elapsed / count * 1_000:.4f} ms/call")
    print(f"  Market structure evaluation throughput: {count / elapsed:,.0f} calls/sec")

    assert await service.ingest_snapshot(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        source_event_type=str(CANDLE_CLOSED),
    ) is not None
