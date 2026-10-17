from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED
from tranalyset.plugins.momentum_strategy.plugin import MomentumStrategy


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_strategy() -> tuple[MomentumStrategy, list[EventEnvelope]]:
    strategy = MomentumStrategy()
    published: list[EventEnvelope] = []

    class MockContext:
        plugin_id = "momentum_strategy"
        config = {"confidence": 0.75, "min_volume_ratio": 1.05, "min_health_score": 50.0}

        async def publish(self, event: EventEnvelope) -> None:
            published.append(event)

        def resolve(self, key: object) -> object:
            return key

    class MockInstallContext:
        plugin_id = "momentum_strategy"

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


async def test_momentum_strategy_publishes_on_positive_evidence() -> None:
    strategy, published = await _started_strategy()

    # indicators show positive trend, momentum and volume
    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "indicators": {"trend_score": 0.5, "momentum_score": 0.6, "volume_ratio": 1.2},
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    # trade health indicates acceptable health
    health_event = EventEnvelope(
        timestamp=_now(),
        source="trade_health_engine",
        event_type=TRADE_HEALTH_UPDATED,
        payload={
            "trade_id": "t-1",
            "symbol": "AAPL",
            "timeframe": "1h",
            "occurred_at": _now().isoformat(),
            "health_score": 70.0,
            "confidence_lower": 65.0,
            "confidence_upper": 75.0,
            "explanations": ("ok",),
            "factors": {},
            "weights": {},
            "evidence_count": 1,
        },
    )
    await strategy.handle_trade_health_updated(health_event)

    candle_event = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "AAPL",
            "timeframe": "1h",
            "close_price": 150.0,
            "open_price": 148.0,
            "high_price": 151.0,
            "low_price": 147.0,
            "volume": 200000.0,
        },
    )
    await strategy.handle_candle_closed(candle_event)

    assert len(published) == 1
    payload = published[0].payload
    assert payload["candidate_type"] == "EntryCandidate"
    assert payload["symbol"] == "AAPL"
    assert payload["confidence"] == pytest.approx(0.75)


async def test_momentum_strategy_gates_on_low_health_or_volume() -> None:
    strategy, published = await _started_strategy()

    # good indicators but low health
    indicators_event = EventEnvelope(
        timestamp=_now(),
        source="technical_analysis",
        event_type=INDICATORS_UPDATED,
        payload={
            "symbol": "TSLA",
            "timeframe": "1h",
            "indicators": {"trend_score": 0.6, "momentum_score": 0.7, "volume_ratio": 1.5},
        },
    )
    await strategy.handle_indicators_updated(indicators_event)

    health_event = EventEnvelope(
        timestamp=_now(),
        source="trade_health_engine",
        event_type=TRADE_HEALTH_UPDATED,
        payload={
            "trade_id": "t-2",
            "symbol": "TSLA",
            "timeframe": "1h",
            "occurred_at": _now().isoformat(),
            "health_score": 40.0,
            "confidence_lower": 30.0,
            "confidence_upper": 50.0,
            "explanations": ("poor liquidity",),
            "factors": {},
            "weights": {},
            "evidence_count": 1,
        },
    )
    await strategy.handle_trade_health_updated(health_event)

    candle_event = EventEnvelope(
        timestamp=_now(),
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "TSLA",
            "timeframe": "1h",
            "close_price": 700.0,
            "open_price": 695.0,
            "high_price": 705.0,
            "low_price": 690.0,
            "volume": 500000.0,
        },
    )
    await strategy.handle_candle_closed(candle_event)

    # should be gated by low health -> no publication
    assert len(published) == 0
