from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.candlestick import PATTERN_DETECTED
from tranalyset.plugins.market_state_engine import (
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
)
from tranalyset.plugins.news import NEWS_ANALYZED
from tranalyset.plugins.risk import RISK_APPROVED
from tranalyset.plugins.technical_analysis import INDICATORS_UPDATED
from tranalyset.plugins.trade_health_engine import (
    CONFIDENCE_CHANGED,
    TRADE_STRENGTH_CHANGED,
    ConfidenceChangedPayload,
    TradeHealthEnginePlugin,
    TradeHealthUpdatedPayload,
    TradeStrengthChangedPayload,
)
from tranalyset.plugins.trade_health_engine.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.trade_health_engine.scoring import TradeHealthEvidence, TradeHealthScorer
from tranalyset.plugins.trade_manager import TRADE_HEALTH_UPDATED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "trade_health_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "trade_health_engine"
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


async def _started_plugin(
    *,
    config: Mapping[str, object] | None = None,
) -> tuple[TradeHealthEnginePlugin, CapturingContext]:
    plugin = TradeHealthEnginePlugin()
    context = CapturingContext(config=config)
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_trade_health_engine_metadata_is_versioned() -> None:
    plugin = TradeHealthEnginePlugin()

    assert plugin.metadata.plugin_id == "trade_health_engine"
    assert plugin.metadata.name == "Trade Health Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_trade_health_engine_installs_published_event_contracts() -> None:
    plugin = TradeHealthEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_trade_health_engine_subscribes_to_required_events() -> None:
    plugin = TradeHealthEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {
        INDICATORS_UPDATED,
        PATTERN_DETECTED,
        MARKET_REGIME_CHANGED,
        TREND_CONFIRMED,
        TREND_WEAKENED,
        NEWS_ANALYZED,
        RISK_APPROVED,
        MOMENTUM_STATE_CHANGED,
    }


def test_trade_health_scorer_calculates_score_interval_and_explanations() -> None:
    evidence = TradeHealthEvidence()
    evidence.update_factor("trend", 85)
    evidence.update_factor("momentum", 75)
    evidence.update_factor("risk", 90)
    scorer = TradeHealthScorer(weights={"risk": 2.0})

    snapshot = scorer.score(evidence, previous=None)

    assert 0 <= snapshot.health_score <= 100
    assert 0 <= snapshot.confidence_lower <= snapshot.confidence_upper <= 100
    assert snapshot.explanations
    assert snapshot.weights["risk"] == 2.0


async def test_trade_health_engine_publishes_required_event_methods() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_trade_health_updated(
        TradeHealthUpdatedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            health_score=72.5,
            previous_health_score=68.0,
            confidence_lower=65.0,
            confidence_upper=80.0,
            factors={"trend": 80.0},
            weights={"trend": 1.0},
            explanations=("Trend evidence improved trade health.",),
            evidence_count=4,
        )
    )
    await plugin.publish_confidence_changed(
        ConfidenceChangedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            confidence_lower=65.0,
            confidence_upper=80.0,
            previous_confidence_lower=60.0,
            previous_confidence_upper=85.0,
            explanations=("More evidence narrowed confidence interval.",),
        )
    )
    await plugin.publish_trade_strength_changed(
        TradeStrengthChangedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            timeframe="1m",
            occurred_at=timestamp,
            strength="Strong",
            previous_strength="Neutral",
            health_score=72.5,
            explanations=("Health crossed strong threshold.",),
        )
    )

    assert [event.event_type for event in context.published] == [
        TRADE_HEALTH_UPDATED,
        CONFIDENCE_CHANGED,
        TRADE_STRENGTH_CHANGED,
    ]
    assert all(event.source == "trade_health_engine" for event in context.published)


async def test_trade_health_engine_integrates_with_event_bus_and_uses_configured_weights() -> None:
    plugin = TradeHealthEnginePlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader([plugin]),
        plugin_config={"trade_health_engine": {"weights": {"risk": 5.0}}},
    )
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    await kernel.event_bus.subscribe(TRADE_HEALTH_UPDATED, collect)
    await kernel.event_bus.subscribe(CONFIDENCE_CHANGED, collect)
    await kernel.event_bus.subscribe(TRADE_STRENGTH_CHANGED, collect)
    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="technical_analysis",
            event_type=INDICATORS_UPDATED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "indicators": {
                    "trend_score": 0.8,
                    "momentum_score": 0.7,
                    "volume_ratio": 1.5,
                    "volatility_score": 0.4,
                    "structure_score": 0.7,
                    "liquidity_score": 0.8,
                    "sector_strength": 0.65,
                    "market_strength": 0.7,
                },
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="candlestick",
            event_type=PATTERN_DETECTED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "pattern": "engulfing",
                "confidence": 0.76,
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=MARKET_REGIME_CHANGED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "regime": "Bull Trend",
                "confidence": 0.8,
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=TREND_CONFIRMED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "state": "Bull Trend Confirmed",
                "confidence": 0.8,
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="news",
            event_type=NEWS_ANALYZED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "headline": "Demand improves",
                "sentiment": "positive",
                "confidence": 0.65,
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "reason": "within configured risk limits",
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=MOMENTUM_STATE_CHANGED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "state": "Strong Momentum",
                "confidence": 0.8,
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("trade_health_engine")
    await kernel.stop()

    health_events = [event for event in published if event.event_type == TRADE_HEALTH_UPDATED]
    confidence_events = [event for event in published if event.event_type == CONFIDENCE_CHANGED]
    strength_events = [event for event in published if event.event_type == TRADE_STRENGTH_CHANGED]
    latest_health = health_events[-1].payload

    assert health_events
    assert confidence_events
    assert strength_events
    assert 0 <= latest_health["health_score"] <= 100
    assert latest_health["confidence_lower"] <= latest_health["confidence_upper"]
    assert latest_health["weights"]["risk"] == 5.0
    assert latest_health["explanations"]
    assert health.status is HealthStatus.HEALTHY


async def test_trade_health_engine_handles_trend_weakened() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_trend_confirmed(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=TREND_CONFIRMED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "confidence": 0.8,
            },
        )
    )
    await plugin.handle_trend_weakened(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=TREND_WEAKENED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "confidence": 0.8,
            },
        )
    )

    health_events = [event for event in context.published if event.event_type == TRADE_HEALTH_UPDATED]
    assert health_events[-1].payload["health_score"] < health_events[0].payload["health_score"]


def test_trade_health_payload_validates_score_range() -> None:
    payload = TradeHealthUpdatedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        timeframe="1m",
        occurred_at=_now(),
        health_score=101,
        previous_health_score=None,
        confidence_lower=70,
        confidence_upper=80,
        explanations=("Score changed.",),
    )

    with pytest.raises(ValueError, match="health_score"):
        payload.to_payload()


def test_trade_health_payloads_do_not_include_entries_or_exits() -> None:
    payload = TradeHealthUpdatedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        timeframe="1m",
        occurred_at=_now(),
        health_score=72.5,
        previous_health_score=68.0,
        confidence_lower=65.0,
        confidence_upper=80.0,
        factors={"trend": 80.0},
        weights={"trend": 1.0},
        explanations=("Trend evidence improved trade health.",),
        evidence_count=4,
    ).to_payload()

    forbidden = {"entry", "exit", "order", "recommendation", "side", "signal"}

    assert forbidden.isdisjoint(payload)
