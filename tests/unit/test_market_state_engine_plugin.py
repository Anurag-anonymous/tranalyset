from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.candlestick import PATTERN_DETECTED
from tranalyset.plugins.market_data import CANDLE_CLOSED, TICK_RECEIVED
from tranalyset.plugins.market_state_engine import (
    LIQUIDITY_CHANGED,
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    SUPPORTED_MARKET_REGIMES,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
    MarketRegimeChangedPayload,
    MarketStateEnginePlugin,
    MomentumStateChangedPayload,
    StateChangePayload,
    VolatilityChangedPayload,
)
from tranalyset.plugins.market_state_engine.classifier import MarketEvidence, MarketStateClassifier
from tranalyset.plugins.market_state_engine.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.news import MARKET_SENTIMENT_CHANGED, NEWS_ANALYZED
from tranalyset.plugins.technical_analysis import INDICATORS_UPDATED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "market_state_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "market_state_engine"
        self.events: list[EventType] = []

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        return None

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[object], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        return None


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_plugin() -> tuple[MarketStateEnginePlugin, CapturingContext]:
    plugin = MarketStateEnginePlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_market_state_engine_metadata_is_versioned() -> None:
    plugin = MarketStateEnginePlugin()

    assert plugin.metadata.plugin_id == "market_state_engine"
    assert plugin.metadata.name == "Market State Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_market_state_engine_installs_published_event_contracts() -> None:
    plugin = MarketStateEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_market_state_engine_subscribes_to_required_evidence_events() -> None:
    plugin = MarketStateEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {
        TICK_RECEIVED,
        CANDLE_CLOSED,
        INDICATORS_UPDATED,
        PATTERN_DETECTED,
        NEWS_ANALYZED,
        MARKET_SENTIMENT_CHANGED,
    }


def test_classifier_supports_all_required_regimes() -> None:
    classifier = MarketStateClassifier()
    cases = [
        MarketEvidence(session_phase="opening"),
        MarketEvidence(session_phase="closing"),
        MarketEvidence(gap_pct=2.0),
        MarketEvidence(gap_pct=-2.0),
        MarketEvidence(structure="false_breakout"),
        MarketEvidence(structure="false_breakdown"),
        MarketEvidence(structure="breakout", price_change_pct=2.0, volatility_score=0.5),
        MarketEvidence(structure="breakdown", price_change_pct=-2.0, volatility_score=0.5),
        MarketEvidence(structure="trend_exhaustion", volatility_score=0.5),
        MarketEvidence(volatility_score=0.9),
        MarketEvidence(volatility_score=0.1),
        MarketEvidence(momentum_score=0.8, volatility_score=0.5),
        MarketEvidence(momentum_score=0.0, volatility_score=0.5),
        MarketEvidence(structure="accumulation", volatility_score=0.5, momentum_score=0.3),
        MarketEvidence(structure="distribution", volatility_score=0.5, momentum_score=0.3),
        MarketEvidence(trend_score=0.7, volatility_score=0.5, momentum_score=0.3),
        MarketEvidence(trend_score=-0.7, volatility_score=0.5, momentum_score=-0.3),
        MarketEvidence(structure="range", volatility_score=0.5, momentum_score=0.3),
        MarketEvidence(price_change_pct=0.1, trend_score=0.1, volatility_score=0.5, momentum_score=0.3),
    ]

    regimes = {classifier.classify(evidence).regime for evidence in cases}

    assert regimes == set(SUPPORTED_MARKET_REGIMES)


async def test_market_state_engine_publishes_required_event_methods() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_market_regime_changed(
        MarketRegimeChangedPayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            regime="Bull Trend",
            previous_regime=None,
            confidence=0.8,
            evidence={"trend_score": 0.7},
        )
    )
    await plugin.publish_trend_confirmed(
        StateChangePayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            state="Bull Trend Confirmed",
            previous_state=None,
            confidence=0.78,
        )
    )
    await plugin.publish_trend_weakened(
        StateChangePayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            state="Trend Weakened",
            previous_state="Bull Trend Confirmed",
            confidence=0.7,
        )
    )
    await plugin.publish_volatility_changed(
        VolatilityChangedPayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            state="High Volatility",
            previous_state="Normal Volatility",
            confidence=0.82,
        )
    )
    await plugin.publish_liquidity_changed(
        StateChangePayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            state="High Liquidity",
            previous_state="Normal Liquidity",
            confidence=0.76,
        )
    )
    await plugin.publish_momentum_state_changed(
        MomentumStateChangedPayload(
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            state="Strong Momentum",
            previous_state="Moderate Momentum",
            confidence=0.8,
        )
    )

    assert [event.event_type for event in context.published] == [
        MARKET_REGIME_CHANGED,
        TREND_CONFIRMED,
        TREND_WEAKENED,
        VOLATILITY_CHANGED,
        LIQUIDITY_CHANGED,
        MOMENTUM_STATE_CHANGED,
    ]
    assert all(event.source == "market_state_engine" for event in context.published)


async def test_market_state_engine_integrates_with_event_bus_and_classifies_changes() -> None:
    plugin = MarketStateEnginePlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    await kernel.event_bus.subscribe(MARKET_REGIME_CHANGED, collect)
    await kernel.event_bus.subscribe(TREND_CONFIRMED, collect)
    await kernel.event_bus.subscribe(VOLATILITY_CHANGED, collect)
    await kernel.event_bus.subscribe(LIQUIDITY_CHANGED, collect)
    await kernel.event_bus.subscribe(MOMENTUM_STATE_CHANGED, collect)
    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="technical_analysis",
            event_type=INDICATORS_UPDATED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1m",
                "indicators": {
                    "trend_score": 0.8,
                    "momentum_score": 0.72,
                    "volatility_score": 0.45,
                    "liquidity_score": 0.8,
                    "volume_ratio": 1.7,
                },
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("market_state_engine")
    await kernel.stop()

    event_types = {event.event_type for event in published}
    regime_events = [event for event in published if event.event_type == MARKET_REGIME_CHANGED]
    assert MARKET_REGIME_CHANGED in event_types
    assert TREND_CONFIRMED in event_types
    assert VOLATILITY_CHANGED in event_types
    assert LIQUIDITY_CHANGED in event_types
    assert MOMENTUM_STATE_CHANGED in event_types
    assert regime_events[0].payload["regime"] == "Strong Momentum"
    assert 0.0 <= regime_events[0].payload["confidence"] <= 1.0
    assert health.status is HealthStatus.HEALTHY


async def test_market_state_engine_processes_all_subscribed_evidence_sources() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_tick_received(
        EventEnvelope.create(
            source="market_data",
            event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "price": 100.0, "size": 10.0},
        )
    )
    await plugin.handle_candle_closed(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1m",
                "open_price": 100.0,
                "high_price": 103.0,
                "low_price": 99.0,
                "close_price": 102.0,
                "volume": 2000.0,
            },
        )
    )
    await plugin.handle_pattern_detected(
        EventEnvelope.create(
            source="candlestick",
            event_type=PATTERN_DETECTED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1m",
                "pattern": "trend_exhaustion",
                "confidence": 0.74,
            },
        )
    )
    await plugin.handle_news_analyzed(
        EventEnvelope.create(
            source="news",
            event_type=NEWS_ANALYZED,
            payload={
                "headline": "Semiconductor demand improves",
                "sentiment": "positive",
                "confidence": 0.7,
                "symbols": ("AAPL",),
            },
        )
    )
    await plugin.handle_market_sentiment_changed(
        EventEnvelope.create(
            source="news",
            event_type=MARKET_SENTIMENT_CHANGED,
            payload={
                "market": "US equities",
                "sentiment": "risk_on",
                "confidence": 0.65,
                "evidence_count": 3,
            },
        )
    )

    assert context.published
    assert plugin.current_classification(symbol="AAPL", timeframe="1m") is not None


def test_market_state_engine_payload_rejects_unsupported_regime() -> None:
    payload = MarketRegimeChangedPayload(
        symbol="AAPL",
        timeframe="1m",
        occurred_at=_now(),
        regime="Buy Now",
        previous_regime=None,
        confidence=0.8,
    )

    with pytest.raises(ValueError, match="Unsupported"):
        payload.to_payload()


def test_market_state_engine_payloads_do_not_include_trading_recommendations() -> None:
    payload = MarketRegimeChangedPayload(
        symbol="AAPL",
        timeframe="1m",
        occurred_at=_now(),
        regime="Bull Trend",
        previous_regime=None,
        confidence=0.8,
        evidence={"trend_score": 0.7},
    ).to_payload()

    forbidden = {"action", "order", "position_size", "recommendation", "side", "signal"}

    assert forbidden.isdisjoint(payload)
