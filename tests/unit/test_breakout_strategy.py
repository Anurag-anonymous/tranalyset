from datetime import UTC, datetime

from tranalyset.contracts.events import EventEnvelope
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.breakout_strategy.plugin import BreakoutStrategy


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_strategy():
    strategy = BreakoutStrategy()
    published = []

    class MockContext:
        plugin_id = "breakout_strategy"
        config = {"confidence": 0.8, "high_lookback": 3, "min_volume_ratio": 1.05, "allowed_regimes": "Bull"}

        async def publish(self, event: EventEnvelope) -> None:
            published.append(event)

        def resolve(self, key: object) -> object:
            return key

    class MockInstallContext:
        plugin_id = "breakout_strategy"

        def is_event_registered(self, event_type) -> bool:
            return False

        def register_event(self, event_type, *, description: str, replace: bool = False) -> None:
            pass

        def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
            pass

        def register_service_factory(self, key: object, factory: object, *, singleton: bool = True, replace: bool = False) -> None:
            pass

    await strategy.install(MockInstallContext())
    await strategy.configure(MockContext())
    await strategy.start(MockContext())
    return strategy, published


async def test_breakout_detects_and_publishes():
    strategy, published = await _started_strategy()

    # indicators with volume ratio
    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "indicators": {"volume_ratio": 1.2},
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    # set market regime to Bull
    regime_event = EventEnvelope(
        timestamp=_now(),
        source="market_state_engine",
        event_type=MARKET_REGIME_CHANGED,
        payload={"symbol": "AAPL", "regime": "Bull Trend Confirmed"},
    )
    await strategy.handle_market_regime_changed(regime_event)

    # feed candles with highs to build up recent highs
    for h in (149.0, 150.0, 151.0):
        candle_event = EventEnvelope(
            timestamp=_now(),
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1h",
                "close_price": h,
                "open_price": h - 1,
                "high_price": h,
                "low_price": h - 2,
                "volume": 100000.0,
            },
        )
        await strategy.handle_candle_closed(candle_event)

    # next candle close above recent highs should trigger
    breakout_candle = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "close_price": 152.0,
            "open_price": 151.0,
            "high_price": 152.0,
            "low_price": 150.0,
            "volume": 200000.0,
        },
    )
    await strategy.handle_candle_closed(breakout_candle)

    assert len(published) == 1
    payload = published[0].payload
    assert payload["candidate_type"] == "EntryCandidate"
    assert payload["symbol"] == "AAPL"


async def test_breakout_gated_on_regime_and_volume():
    strategy, published = await _started_strategy()

    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "TSLA",
            "timeframe": "1h",
            "indicators": {"volume_ratio": 0.9},
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    # regime not allowed
    regime_event = EventEnvelope(
        timestamp=_now(),
        source="market_state_engine",
        event_type=MARKET_REGIME_CHANGED,
        payload={"symbol": "TSLA", "regime": "Range Bound"},
    )
    await strategy.handle_market_regime_changed(regime_event)

    # build highs
    for h in (699.0, 700.0, 701.0):
        candle_event = EventEnvelope(
            timestamp=_now(),
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "TSLA",
                "timeframe": "1h",
                "close_price": h,
                "open_price": h - 1,
                "high_price": h,
                "low_price": h - 2,
                "volume": 100000.0,
            },
        )
        await strategy.handle_candle_closed(candle_event)

    # breakout candle but volume_ratio low and regime not allowed -> no publish
    breakout_candle = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "TSLA",
            "timeframe": "1h",
            "close_price": 702.0,
            "open_price": 701.0,
            "high_price": 702.0,
            "low_price": 699.0,
            "volume": 100000.0,
        },
    )
    await strategy.handle_candle_closed(breakout_candle)

    assert len(published) == 0
