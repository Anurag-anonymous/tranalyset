from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.plugins.context_engine import (
    ContextEngineSettings,
    ContextEnginePlugin,
    ContextEngineService,
    MarketContextUpdatedPayload,
    MARKET_CONTEXT_UPDATED,
    SUBSCRIBED_EVENTS,
)
from tranalyset.plugins.context_engine.service import ContextComponent
from tranalyset.plugins.feature_engine.contracts import FeatureSet, FEATURES_UPDATED
from tranalyset.plugins.indicator_engine.contracts import IndicatorsUpdatedPayload, INDICATORS_UPDATED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.market_structure_engine.contracts import MARKET_STRUCTURE_UPDATED
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.news.contracts import NEWS_ANALYZED
from tranalyset.plugins.pattern_intelligence_engine.contracts import PATTERN_DETECTED


class _CapturingContext:
    def __init__(self, services: Mapping[object, object], *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "context_engine"
        self.config = config or {}
        self._services = dict(services)
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return self._services[key]


class _InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "context_engine"
        self.events: list[object] = []
        self.services: dict[object, object] = {}

    def register_event(self, event_type: object, *, description: str, replace: bool = False) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        self.services[key] = instance

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[object], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        self.services[key] = factory


def _now(offset_minutes: int = 0) -> datetime:
    return datetime(2026, 8, 2, 9, 30, tzinfo=UTC) + timedelta(minutes=offset_minutes)


def test_context_contracts_validate_payloads() -> None:
    timestamp = _now()
    payload = MarketContextUpdatedPayload(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=timestamp,
        trend_context={"state": "Bull Trend", "confidence": 0.81, "evidence": {}},
        momentum_context={"state": "Strong Momentum", "confidence": 0.79, "evidence": {}},
        volatility_context={"state": "High Volatility", "confidence": 0.66, "evidence": {}},
        volume_context={"state": "Expanding Volume", "confidence": 0.74, "evidence": {}},
        liquidity_context={"state": "Normal Liquidity", "confidence": 0.63, "evidence": {}},
        market_context={"state": "Breakout", "confidence": 0.77, "evidence": {}},
        sector_context={"state": "Technology", "confidence": 0.72, "evidence": {}},
        session_context={"state": "Regular Session", "confidence": 0.61, "evidence": {}},
        risk_context={"state": "Moderate Risk", "confidence": 0.58, "evidence": {}},
        context_confidence=0.74,
        context_quality=0.69,
        evidence={"sources": 4, "consensus": True},
        source_event_type=str(INDICATORS_UPDATED),
    ).to_payload()

    assert payload["context_confidence"] == 0.74
    assert payload["context_quality"] == 0.69
    assert payload["trend_context"]["state"] == "Bull Trend"
    assert "recommendation" not in payload

    with pytest.raises(ValueError, match="context_confidence"):
        MarketContextUpdatedPayload(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe="1m",
            observed_at=timestamp,
            trend_context={"state": "Bull Trend", "confidence": 0.81, "evidence": {}},
            momentum_context={"state": "Strong Momentum", "confidence": 0.79, "evidence": {}},
            volatility_context={"state": "High Volatility", "confidence": 0.66, "evidence": {}},
            volume_context={"state": "Expanding Volume", "confidence": 0.74, "evidence": {}},
            liquidity_context={"state": "Normal Liquidity", "confidence": 0.63, "evidence": {}},
            market_context={"state": "Breakout", "confidence": 0.77, "evidence": {}},
            sector_context={"state": "Technology", "confidence": 0.72, "evidence": {}},
            session_context={"state": "Regular Session", "confidence": 0.61, "evidence": {}},
            risk_context={"state": "Moderate Risk", "confidence": 0.58, "evidence": {}},
            context_confidence=1.5,
            context_quality=0.69,
            evidence={"sources": 4, "consensus": True},
            source_event_type=str(INDICATORS_UPDATED),
        ).to_payload()


def test_context_plugin_metadata_and_subscriptions() -> None:
    plugin = ContextEnginePlugin()

    assert plugin.metadata.plugin_id == "context_engine"
    assert plugin.metadata.depends_on == frozenset(
        {
            "feature_engine",
            "indicator_engine",
            "pattern_intelligence_engine",
            "market_structure_engine",
            "news",
            "market_state_engine",
        }
    )
    assert set(plugin.subscriptions()) == set(SUBSCRIBED_EVENTS)


async def test_context_service_combines_market_evidence_into_snapshot() -> None:
    service = ContextEngineService()

    await service.observe_indicators(
        IndicatorsUpdatedPayload(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe="1m",
            observed_at=_now(),
            indicators={
                "EMA": 104.2,
                "SMA": 101.5,
                "MACD": (1.5, 1.0, 0.5),
                "RSI": 66.0,
                "ATR": 8.0,
            },
            versions={"EMA": "1.0", "SMA": "1.0", "MACD": "1.0", "RSI": "1.0", "ATR": "1.0"},
            source_event_type=str(INDICATORS_UPDATED),
        )
    )
    await service.observe_features(
        FeatureSet(
            feature_set_id="feature-aapl",
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe="1m",
            observed_at=_now(1),
            source_event_type=str(FEATURES_UPDATED),
            features={
                "relative_volume": 1.35,
                "volume_ratio": 1.28,
                "trend_window": 0.08,
                "momentum_window": 0.06,
                "session_phase": "regular",
                "liquidity_metrics": 0.72,
            },
            metadata={"sector": "Technology"},
            generated_at=_now(1),
        )
    )
    await service.observe_pattern(
        {
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "timeframe": "1m",
            "observed_at": _now(2).isoformat(),
            "pattern": "Bullish Engulfing",
            "category": "candlestick",
            "confidence": 0.78,
            "validity": True,
            "source_event_type": str(PATTERN_DETECTED),
        }
    )
    await service.observe_structure(
        {
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "timeframe": "1m",
            "observed_at": _now(3).isoformat(),
            "structure": "Bull Structure",
            "internal_structure": "Trend Structure",
            "external_structure": "External Bull Structure",
            "confidence": 0.82,
            "detected": ["Higher High", "Higher Low", "Break of Structure"],
            "source_event_type": str(MARKET_STRUCTURE_UPDATED),
        },
        source_event_type=str(MARKET_STRUCTURE_UPDATED),
    )
    await service.observe_market_state(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        state="Bull Trend",
        confidence=0.84,
        evidence={"regime": "Bull Trend", "session_phase": "regular"},
        observed_at=_now(4),
        source_event_type=str(MARKET_REGIME_CHANGED),
    )
    await service.observe_news(
        symbols=("AAPL",),
        sentiment="positive",
        confidence=0.76,
        evidence={"sector": "Technology", "headline": "Positive guidance"},
        observed_at=_now(5),
        source_event_type=str(NEWS_ANALYZED),
    )

    snapshot = await service.snapshot_for(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        source_event_type="ContextProbe",
    )

    assert snapshot.trend_context.state == "Bull Trend"
    assert snapshot.momentum_context.state == "Strong Momentum"
    assert snapshot.market_context.state in {"Bull Trend", "Breakout"}
    assert snapshot.sector_context.state == "Technology"
    assert snapshot.context_confidence > 0.5
    assert snapshot.context_quality > 0.5


def test_context_service_evicts_least_recently_used_keys_at_limit() -> None:
    service = ContextEngineService(settings=ContextEngineSettings(max_tracked_keys=2))
    first = ("AAPL", "NASDAQ", "1m")
    second = ("MSFT", "NASDAQ", "1m")
    third = ("NVDA", "NASDAQ", "1m")
    component = ContextComponent(
        state="Bull Trend",
        confidence=0.8,
        evidence={},
        observed_at=_now(),
        source_event_type=str(INDICATORS_UPDATED),
    )

    service._register_key(first)
    service._books.trend[first] = component
    service._register_key(second)
    service._register_key(first)
    service._register_key(third)

    assert tuple(service._known_keys) == (first, third)
    assert second not in service._books.trend
    assert second not in service._known_keys_by_symbol


async def test_context_plugin_publishes_market_context_updated() -> None:
    plugin = ContextEnginePlugin()
    install_context = _InstallContext()
    await plugin.install(install_context)

    context = _CapturingContext({})
    await plugin.configure(context)
    await plugin.start(context)

    await plugin.handle_indicators_updated(
        EventEnvelope.create(
            source="indicator_engine",
            event_type=INDICATORS_UPDATED,
            payload={
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "observed_at": _now().isoformat(),
                "indicators": {
                    "EMA": 104.2,
                    "SMA": 101.5,
                    "MACD": (1.5, 1.0, 0.5),
                    "RSI": 66.0,
                    "ATR": 8.0,
                },
                "versions": {"EMA": "1.0", "SMA": "1.0", "MACD": "1.0", "RSI": "1.0", "ATR": "1.0"},
            },
        )
    )
    await plugin.handle_features_updated(
        EventEnvelope.create(
            source="feature_engine",
            event_type=FEATURES_UPDATED,
            payload={
                "feature_set_id": "feature-aapl",
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "observed_at": _now(1).isoformat(),
                "generated_at": _now(1).isoformat(),
                "source_event_type": str(FEATURES_UPDATED),
                "features": {
                    "relative_volume": 1.35,
                    "volume_ratio": 1.28,
                    "trend_window": 0.08,
                    "momentum_window": 0.06,
                    "session_phase": "regular",
                    "liquidity_metrics": 0.72,
                },
                "metadata": {"sector": "Technology"},
            },
        )
    )

    await plugin.stop(context)

    assert [event.event_type for event in context.published] == [MARKET_CONTEXT_UPDATED, MARKET_CONTEXT_UPDATED]
    assert all(event.source == "context_engine" for event in context.published)
    assert context.published[-1].payload["trend_context"]["state"] == "Bull Trend"
    assert context.published[-1].payload["context_confidence"] > 0.5


async def test_context_plugin_handles_market_regime_evidence_mapping() -> None:
    plugin = ContextEnginePlugin()
    await plugin.install(_InstallContext())
    context = _CapturingContext({})
    await plugin.configure(context)
    await plugin.start(context)

    await plugin.handle_market_regime_changed(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=MARKET_REGIME_CHANGED,
            payload={
                "symbol": "NIFTY",
                "exchange": "NSE",
                "timeframe": "5m",
                "regime": "Bull Trend",
                "confidence": 0.8,
                "evidence": {"trend_strength": 0.75, "source": "market_state_engine"},
                "occurred_at": _now().isoformat(),
            },
        )
    )

    await plugin.stop(context)

    assert context.published[0].event_type == MARKET_CONTEXT_UPDATED


async def test_context_health_reflects_context_generation() -> None:
    plugin = ContextEnginePlugin()
    await plugin.install(_InstallContext())
    context = _CapturingContext({})
    await plugin.configure(context)
    initial_health = await plugin.health_check()
    await plugin.start(context)
    await plugin.handle_indicators_updated(
        EventEnvelope.create(
            source="indicator_engine",
            event_type=INDICATORS_UPDATED,
            payload={
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "observed_at": _now().isoformat(),
                "indicators": {"EMA": 104.2, "SMA": 101.5, "RSI": 66.0},
                "versions": {"EMA": "1.0", "SMA": "1.0", "RSI": "1.0"},
            },
        )
    )
    ready_health = await plugin.health_check()
    await plugin.stop(context)

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY
