from datetime import UTC, datetime, timedelta

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.feature_engine import FeatureEngineeringPlugin
from tranalyset.plugins.indicator_engine import IndicatorEnginePlugin
from tranalyset.plugins.market_data import CandleClosedPayload, MarketDataPlugin
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.market_state_engine import MarketStateEnginePlugin
from tranalyset.plugins.pattern_intelligence_engine import (
    PATTERN_DETECTED,
    PatternIntelligenceEnginePlugin,
)
from tranalyset.plugins.pattern_intelligence_engine.contracts import PatternUpdatedPayload
from tranalyset.plugins.pattern_intelligence_engine.service import PatternIntelligenceService


def test_pattern_updated_payload_extends_base_payload() -> None:
    observed_at = datetime(2026, 8, 3, 9, 30, tzinfo=UTC)
    payload = PatternUpdatedPayload(
        symbol="NIFTY",
        exchange="NSE",
        timeframe="5m",
        observed_at=observed_at,
        pattern_id="pattern-1",
        pattern="Doji",
        category="candlestick",
        confidence=0.8,
        strength=1.0,
        age_bars=2,
        validity=True,
        historical_success_rate=0.0,
        reliability=0.5,
        current_context={},
        volume_confirmation=0.0,
        trend_confirmation=0.0,
        volatility=0.0,
        market_regime=None,
        evidence={},
        source_event_type="TimeframeCandleClosed",
        previous_confidence=0.7,
        previous_strength=0.9,
    )

    result = payload.to_payload()

    assert result["symbol"] == "NIFTY"
    assert result["previous_confidence"] == 0.7
    assert result["previous_strength"] == 0.9


async def test_pattern_plugin_runs_with_declared_dependencies_and_publishes_detections() -> None:
    pattern_plugin = PatternIntelligenceEnginePlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader(
            [
                pattern_plugin,
                MarketStateEnginePlugin(),
                IndicatorEnginePlugin(),
                FeatureEngineeringPlugin(),
                MarketDataPlugin(),
            ]
        ),
        plugin_config={
            "pattern_intelligence_engine": {
                "min_confidence": 0.0,
                "min_strength": 0.0,
                "max_tracked_series": 1,
            }
        },
    )
    detected: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        detected.append(event)

    await kernel.event_bus.subscribe(PATTERN_DETECTED, collect)
    await kernel.start()

    assert kernel._calendar_service._running
    assert kernel._aggregation_engine._running

    start = datetime(2026, 8, 3, 9, 30, tzinfo=UTC)
    candles = (
        CandleClosedPayload(
            symbol="AAPL",
            timeframe="1m",
            opened_at=start,
            closed_at=start + timedelta(minutes=1),
            open_price=100.0,
            high_price=101.0,
            low_price=98.0,
            close_price=99.0,
            volume=100.0,
            venue="NASDAQ",
        ),
        CandleClosedPayload(
            symbol="AAPL",
            timeframe="1m",
            opened_at=start + timedelta(minutes=1),
            closed_at=start + timedelta(minutes=2),
            open_price=99.0,
            high_price=99.5,
            low_price=98.5,
            close_price=99.0,
            volume=110.0,
            venue="NASDAQ",
        ),
        CandleClosedPayload(
            symbol="MSFT",
            timeframe="1m",
            opened_at=start + timedelta(minutes=2),
            closed_at=start + timedelta(minutes=3),
            open_price=100.0,
            high_price=101.0,
            low_price=99.0,
            close_price=100.5,
            volume=90.0,
            venue="NASDAQ",
        ),
    )
    for candle in candles:
        await kernel.publish_event(
            EventEnvelope.create(
                source="test-market-feed",
                event_type=CANDLE_CLOSED,
                payload=candle.to_payload(),
            )
        )
    await kernel.event_bus.wait_until_idle()
    await kernel.stop()

    assert detected
    assert any(event.payload["pattern"] == "Doji" for event in detected)
    pattern_service = kernel.container.resolve(PatternIntelligenceService)
    assert (await pattern_service.health_snapshot())["tracked_series"] == 1
    assert ("AAPL", "NASDAQ", "1m") not in pattern_service._candles_seen
    assert not kernel._calendar_service._running
    assert not kernel._aggregation_engine._running
