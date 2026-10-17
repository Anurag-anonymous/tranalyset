from datetime import UTC, datetime

from tranalyset.contracts.events import EventEnvelope
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.plugins.mean_reversion_strategy.plugin import MeanReversionStrategy


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_strategy():
    strategy = MeanReversionStrategy()
    published = []

    class MockContext:
        plugin_id = "mean_reversion_strategy"
        config = {
            "confidence": 0.75,
            "rsi_oversold": 30.0,
            "atr_multiplier": 1.0,
            "min_volatility": 0.5,
        }

        async def publish(self, event: EventEnvelope) -> None:
            published.append(event)

        def resolve(self, key: object) -> object:
            return key

    class MockInstallContext:
        plugin_id = "mean_reversion_strategy"

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


async def test_mean_reversion_publishes_on_oversold_and_volatility():
    strategy, published = await _started_strategy()

    # indicators: RSI oversold, volatility moderate, ATR defined
    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "indicators": {
                "rsi": 25.0,
                "atr": 2.0,
                "volatility": 5.0,
            },
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    # candle with price near low (mean reversion trigger)
    candle_event = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "close_price": 148.8,
            "open_price": 149.0,
            "high_price": 151.5,
            "low_price": 148.5,
            "volume": 100000.0,
        },
    )
    await strategy.handle_candle_closed(candle_event)

    assert len(published) == 1
    payload = published[0].payload
    assert payload["candidate_type"] == "EntryCandidate"
    assert payload["symbol"] == "AAPL"
    assert payload["confidence"] == 0.75


async def test_mean_reversion_gated_on_high_volatility():
    strategy, published = await _started_strategy()

    # indicators: RSI oversold but volatility too high
    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "TSLA",
            "timeframe": "1h",
            "indicators": {
                "rsi": 28.0,
                "atr": 5.0,
                "volatility": 25.0,
            },
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    # candle with price near low (but volatility too high -> gated)
    candle_event = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "TSLA",
            "timeframe": "1h",
            "close_price": 700.0,
            "open_price": 699.0,
            "high_price": 705.0,
            "low_price": 695.0,
            "volume": 200000.0,
        },
    )
    await strategy.handle_candle_closed(candle_event)

    # no publish due to high volatility
    assert len(published) == 0


async def test_mean_reversion_gated_on_rsi_not_oversold():
    strategy, published = await _started_strategy()

    # indicators: RSI not oversold
    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "MSFT",
            "timeframe": "1h",
            "indicators": {
                "rsi": 55.0,
                "atr": 1.5,
                "volatility": 3.0,
            },
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    # candle with price near low (but RSI not oversold -> gated)
    candle_event = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "MSFT",
            "timeframe": "1h",
            "close_price": 320.0,
            "open_price": 319.0,
            "high_price": 321.5,
            "low_price": 318.5,
            "volume": 150000.0,
        },
    )
    await strategy.handle_candle_closed(candle_event)

    # no publish due to RSI not oversold
    assert len(published) == 0
