"""Benchmark comparisons for the Probability Engine.

Run with: python -m pytest tests/benchmarks/ -v --tb=short -s
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.probability_engine import ProbabilityEngineService
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED


@pytest.mark.asyncio
async def test_benchmark_probability_aggregation_latency() -> None:
    service = ProbabilityEngineService()
    key = {
        "trade_id": "trade-aapl",
        "symbol": "AAPL",
        "exchange": "NASDAQ",
        "timeframe": "1m",
    }

    await service.observe_market_context(
        {
            **key,
            "observed_at": datetime(2026, 8, 2, 10, 0, tzinfo=UTC).isoformat(),
            "source_event_type": str(MARKET_CONTEXT_UPDATED),
            "trend_context": {"state": "Bull Trend", "confidence": 0.84, "evidence": {}},
            "momentum_context": {"state": "Strong Momentum", "confidence": 0.8, "evidence": {}},
            "volatility_context": {"state": "High Volatility", "confidence": 0.64, "evidence": {}},
            "volume_context": {"state": "Expanding Volume", "confidence": 0.74, "evidence": {}},
            "liquidity_context": {"state": "High Liquidity", "confidence": 0.7, "evidence": {}},
            "market_context": {"state": "Breakout", "confidence": 0.8, "evidence": {}},
            "sector_context": {"state": "Technology", "confidence": 0.68, "evidence": {}},
            "session_context": {"state": "Regular Session", "confidence": 0.62, "evidence": {}},
            "risk_context": {"state": "Low Risk", "confidence": 0.58, "evidence": {}},
            "context_confidence": 0.81,
            "context_quality": 0.74,
        }
    )

    await service.observe_trade_health(
        {
            **key,
            "occurred_at": datetime(2026, 8, 2, 10, 1, tzinfo=UTC).isoformat(),
            "source_event_type": str(TRADE_HEALTH_UPDATED),
            "health_score": 79.0,
            "confidence_lower": 0.66,
            "confidence_upper": 0.85,
            "factors": {},
        }
    )

    payload = {
        **key,
        "observed_at": datetime(2026, 8, 2, 10, 2, tzinfo=UTC).isoformat(),
        "source_event_type": str(MARKET_CONTEXT_UPDATED),
        "trend_context": {"state": "Bull Trend", "confidence": 0.84, "evidence": {}},
        "momentum_context": {"state": "Strong Momentum", "confidence": 0.8, "evidence": {}},
        "volatility_context": {"state": "High Volatility", "confidence": 0.64, "evidence": {}},
        "volume_context": {"state": "Expanding Volume", "confidence": 0.74, "evidence": {}},
        "liquidity_context": {"state": "High Liquidity", "confidence": 0.7, "evidence": {}},
        "market_context": {"state": "Breakout", "confidence": 0.8, "evidence": {}},
        "sector_context": {"state": "Technology", "confidence": 0.68, "evidence": {}},
        "session_context": {"state": "Regular Session", "confidence": 0.62, "evidence": {}},
        "risk_context": {"state": "Low Risk", "confidence": 0.58, "evidence": {}},
        "context_confidence": 0.81,
        "context_quality": 0.74,
    }

    await service.observe_market_context(payload)

    count = 2_000
    start = time.perf_counter()
    for offset in range(count):
        payload["observed_at"] = (datetime(2026, 8, 2, 10, 2, tzinfo=UTC) + timedelta(milliseconds=offset)).isoformat()
        await service.observe_market_context(payload)
    elapsed = time.perf_counter() - start

    print(f"\n  Probability aggregation latency: {elapsed / count * 1_000:.4f} ms/call")
    print(f"  Probability aggregation throughput: {count / elapsed:,.0f} calls/sec")

    assert await service.snapshot_for(
        trade_id="trade-aapl",
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        source_event_type="Probe",
    ) is not None
