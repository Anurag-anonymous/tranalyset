from datetime import UTC, datetime
from typing import Any

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, CandleClosedPayload
from tranalyset.plugins.signal_engine.contracts import CANDIDATE_TYPES, TradeSignalCandidate
from tranalyset.plugins.technical_analysis.contracts import (
    INDICATORS_UPDATED,
    IndicatorsUpdatedPayload,
)
from tranalyset.strategy_sdk import (
    StrategyMetadata,
    StrategyParameter,
    StrategyPlugin,
)


class SimpleMovingAverageStrategy(StrategyPlugin):
    def __init__(self) -> None:
        metadata = StrategyMetadata(
            strategy_id="simple_moving_average",
            name="Simple Moving Average",
            version="1.0.0",
            description="Entry signals when price crosses above a moving average.",
            parameters=(
                StrategyParameter(
                    name="ma_period",
                    parameter_type="int",
                    default_value=20,
                    min_value=5,
                    max_value=200,
                    description="Moving average period in candles.",
                ),
                StrategyParameter(
                    name="confidence",
                    parameter_type="float",
                    default_value=0.65,
                    min_value=0.0,
                    max_value=1.0,
                    description="Confidence threshold for entry signals.",
                ),
            ),
        )
        super().__init__(metadata=metadata)
        self._last_indicators: dict[str, dict[str, float]] = {}

    @property
    def _subscribes_to_events(self) -> tuple[EventType, ...]:
        return (CANDLE_CLOSED, INDICATORS_UPDATED)

    def subscriptions(self) -> dict[EventType, tuple[Any, ...]]:
        return {
            CANDLE_CLOSED: (self.handle_candle_closed,),
            INDICATORS_UPDATED: (self.handle_indicators_updated,),
        }

    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        indicators = self._last_indicators.get(symbol, {})
        if "sma" not in indicators:
            return
        sma = float(indicators["sma"])
        close_price = float(payload.get("close_price", 0.0))
        if close_price > sma:
            await self.publish_candidate(
                symbol=symbol,
                timeframe=str(payload.get("timeframe", "1h")),
                confidence=self._configuration.get("confidence", 0.65),
                reasoning=("Price closed above moving average.",),
                supporting_evidence={"sma": sma, "close_price": close_price},
                reward_risk_estimate=2.0,
                candidate_type="EntryCandidate",
            )

    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        indicators = payload.get("indicators", {})
        if indicators:
            self._last_indicators[symbol] = dict(indicators)


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_strategy() -> tuple[SimpleMovingAverageStrategy, list[EventEnvelope]]:
    strategy = SimpleMovingAverageStrategy()
    published: list[EventEnvelope] = []

    class MockContext:
        plugin_id = "simple_moving_average"
        config = {"ma_period": 20, "confidence": 0.70}

        async def publish(self, event: EventEnvelope) -> None:
            published.append(event)

        def resolve(self, key: object) -> object:
            return key

    class MockInstallContext:
        plugin_id = "simple_moving_average"

        def is_event_registered(self, event_type: EventType) -> bool:
            return False

        def register_event(
            self,
            event_type: EventType,
            *,
            description: str,
            replace: bool = False,
        ) -> None:
            pass

        def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
            pass

        def register_service_factory(
            self,
            key: object,
            factory: object,
            *,
            singleton: bool = True,
            replace: bool = False,
        ) -> None:
            pass

    await strategy.install(MockInstallContext())
    await strategy.configure(MockContext())
    await strategy.start(MockContext())
    return strategy, published


def test_strategy_metadata_validates_required_fields() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        StrategyMetadata(
            strategy_id="",
            name="Test",
            version="1.0.0",
            description="Test strategy",
        )

    with pytest.raises(ValueError, match="must not be empty"):
        StrategyMetadata(
            strategy_id="test",
            name="",
            version="1.0.0",
            description="Test strategy",
        )


def test_strategy_parameter_validates_type() -> None:
    with pytest.raises(ValueError, match="Unsupported parameter type"):
        StrategyParameter(
            name="threshold",
            parameter_type="invalid_type",
            default_value=0.5,
        )


def test_strategy_plugin_metadata_is_versioned() -> None:
    strategy = SimpleMovingAverageStrategy()

    assert strategy.metadata.plugin_id == "simple_moving_average"
    assert strategy.metadata.name == "Simple Moving Average"
    assert strategy.metadata.version == "1.0.0"
    assert strategy.strategy_metadata.description == "Entry signals when price crosses above a moving average."


def test_strategy_plugin_defines_parameters() -> None:
    strategy = SimpleMovingAverageStrategy()

    params = strategy.strategy_metadata.parameters
    assert len(params) == 2
    assert params[0].name == "ma_period"
    assert params[0].parameter_type == "int"
    assert params[0].default_value == 20
    assert params[1].name == "confidence"
    assert params[1].parameter_type == "float"


async def test_strategy_plugin_publishes_entry_candidates_on_market_signals() -> None:
    strategy, published = await _started_strategy()

    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "indicators": {"sma": 150.0, "rsi": 55.0},
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    candle_event = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "close_price": 152.0,
            "open_price": 148.0,
            "high_price": 153.0,
            "low_price": 147.0,
            "volume": 1000000.0,
        },
    )
    await strategy.handle_candle_closed(candle_event)

    assert len(published) == 1
    assert published[0].event_type == EventType("TradeSignalGenerated")
    payload = published[0].payload
    assert payload["candidate_type"] == "EntryCandidate"
    assert payload["symbol"] == "AAPL"
    assert payload["confidence"] == 0.70


async def test_strategy_performance_stats_track_candidates_generated() -> None:
    strategy, published = await _started_strategy()

    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "MSFT",
            "timeframe": "1h",
            "indicators": {"sma": 320.0},
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    for i in range(3):
        candle_event = EventEnvelope(
            timestamp=_now(),
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "MSFT",
                "timeframe": "1h",
                "close_price": 325.0 + i,
                "open_price": 320.0,
                "high_price": 330.0,
                "low_price": 318.0,
                "volume": 1000000.0,
            },
        )
        await strategy.handle_candle_closed(candle_event)

    stats = strategy.performance_stats
    assert stats["candidates_generated"] == 3
    assert stats["by_type"]["EntryCandidate"] == 3


async def test_strategy_health_degrades_until_first_candidate() -> None:
    strategy = SimpleMovingAverageStrategy()

    class MockContext:
        plugin_id = "simple_moving_average"
        config = {}

        async def publish(self, event: EventEnvelope) -> None:
            pass

        def resolve(self, key: object) -> object:
            return key

    class MockInstallContext:
        plugin_id = "simple_moving_average"

        def is_event_registered(self, event_type: EventType) -> bool:
            return False

        def register_event(
            self,
            event_type: EventType,
            *,
            description: str,
            replace: bool = False,
        ) -> None:
            pass

        def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
            pass

        def register_service_factory(
            self,
            key: object,
            factory: object,
            *,
            singleton: bool = True,
            replace: bool = False,
        ) -> None:
            pass

    await strategy.install(MockInstallContext())
    await strategy.configure(MockContext())
    await strategy.start(MockContext())

    health_before = await strategy.health_check()
    assert health_before.status is HealthStatus.DEGRADED

    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={"symbol": "AAPL", "timeframe": "1h", "indicators": {"sma": 150.0}},
    )
    await strategy.handle_indicators_updated(indicators_event)

    candle_event = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "close_price": 152.0,
            "open_price": 148.0,
            "high_price": 153.0,
            "low_price": 147.0,
            "volume": 1000000.0,
        },
    )
    await strategy.handle_candle_closed(candle_event)

    health_after = await strategy.health_check()
    assert health_after.status is HealthStatus.HEALTHY


async def test_strategy_integrates_as_plugin_with_kernel() -> None:
    strategy = SimpleMovingAverageStrategy()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([strategy]))
    candidates: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        candidates.append(event)

    await kernel.event_bus.subscribe(EventType("TradeSignalGenerated"), collect)
    await kernel.start()

    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="technical_analysis",
            event_type=INDICATORS_UPDATED,
            payload={"symbol": "AAPL", "timeframe": "1h", "indicators": {"sma": 155.0}},
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1h",
                "close_price": 158.0,
                "open_price": 154.0,
                "high_price": 159.0,
                "low_price": 153.0,
                "volume": 2000000.0,
                "venue": "NASDAQ",
            },
        )
    )
    await kernel.event_bus.wait_until_idle()

    health = await kernel.plugin_health("simple_moving_average")
    await kernel.stop()

    assert len(candidates) == 1
    assert candidates[0].payload["symbol"] == "AAPL"
    assert health.status is HealthStatus.HEALTHY
    assert health.details["candidates_generated"] == 1


def test_strategy_candidate_payload_validates_confidence() -> None:
    from unittest.mock import AsyncMock

    strategy = SimpleMovingAverageStrategy()
    strategy._context = AsyncMock()

    with pytest.raises(ValueError, match="confidence must be between"):
        import asyncio

        asyncio.run(
            strategy.publish_candidate(
                symbol="AAPL",
                timeframe="1h",
                confidence=1.5,
                reasoning=("Test",),
                supporting_evidence={"test": 1.0},
                reward_risk_estimate=2.0,
            )
        )


def test_strategy_candidate_payload_validates_reward_risk() -> None:
    from unittest.mock import AsyncMock

    strategy = SimpleMovingAverageStrategy()
    strategy._context = AsyncMock()

    with pytest.raises(ValueError, match="reward_risk_estimate must be greater than zero"):
        import asyncio

        asyncio.run(
            strategy.publish_candidate(
                symbol="AAPL",
                timeframe="1h",
                confidence=0.7,
                reasoning=("Test",),
                supporting_evidence={"test": 1.0},
                reward_risk_estimate=-1.0,
            )
        )
