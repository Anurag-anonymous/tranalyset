from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED, FeatureSet
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED, IndicatorsUpdatedPayload
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.market_structure_engine import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
    CharacterChangedPayload,
    LiquidityDetectedPayload,
    MarketStructureEnginePlugin,
    MarketStructureService,
    MarketStructureSettings,
    MarketStructureUpdatedPayload,
    StructureBreakPayload,
    SUBSCRIBED_EVENTS,
)
from tranalyset.plugins.pattern_intelligence_engine.contracts import PATTERN_DETECTED


class _CapturingContext:
    def __init__(self, services: Mapping[object, object], *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "market_structure_engine"
        self.config = config or {}
        self._services = dict(services)
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return self._services[key]


class _InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "market_structure_engine"
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
    return datetime(2026, 8, 1, 9, 30, tzinfo=UTC) + timedelta(minutes=offset_minutes)


async def _bundle() -> tuple[MarketDataRepository, IndicatorCache, TimeframeAggregationEngine]:
    backend = InMemoryStorageBackend()
    repo = MarketDataRepository(backend)
    bus = object()
    cache = IndicatorCache(repository=repo, event_bus=bus)  # type: ignore[arg-type]
    aggregation = TimeframeAggregationEngine(repository=repo, event_bus=bus)  # type: ignore[arg-type]
    return repo, cache, aggregation


async def _seed_bullish_structure(repo: MarketDataRepository) -> None:
    candles = (
        (100.0, 101.0, 99.5, 100.4, 1_000.0),
        (100.4, 101.8, 100.2, 101.2, 1_100.0),
        (101.2, 103.0, 101.0, 102.5, 1_200.0),
        (102.0, 102.4, 101.5, 101.9, 1_050.0),
        (101.9, 102.8, 101.7, 102.4, 1_150.0),
        (102.4, 104.5, 102.0, 103.9, 1_400.0),
    )
    for index, (open_price, high, low, close, volume) in enumerate(candles):
        await repo.append_candle(
            Candle(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=_now(index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                trades=10 + index,
            )
        )


async def _seed_liquidity_sweep(repo: MarketDataRepository) -> None:
    candles = (
        (100.0, 101.0, 99.5, 100.6, 900.0),
        (100.6, 102.0, 100.2, 101.7, 950.0),
        (101.7, 101.8, 100.8, 101.0, 910.0),
        (101.0, 102.01, 100.9, 101.55, 980.0),
        (101.55, 101.6, 101.0, 101.2, 940.0),
        (101.2, 102.3, 100.95, 101.45, 1_050.0),
    )
    for index, (open_price, high, low, close, volume) in enumerate(candles):
        await repo.append_candle(
            Candle(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=_now(20 + index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                trades=15 + index,
            )
        )


def test_market_structure_contracts_validate_payloads() -> None:
    timestamp = _now()
    payload = MarketStructureUpdatedPayload(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=timestamp,
        structure="Bull Structure",
        internal_structure="Trend Structure",
        external_structure="External Bull Structure",
        confidence=0.82,
        source_event_type=str(CANDLE_CLOSED),
    ).to_payload()

    assert payload["structure"] == "Bull Structure"
    assert payload["confidence"] == 0.82
    assert payload["timeframe"] == "1m"

    with pytest.raises(ValueError, match="confidence"):
        MarketStructureUpdatedPayload(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe="1m",
            observed_at=timestamp,
            structure="Bull Structure",
            internal_structure="Trend Structure",
            external_structure="External Bull Structure",
            confidence=1.3,
            source_event_type=str(CANDLE_CLOSED),
        ).to_payload()

    break_payload = StructureBreakPayload(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=timestamp,
        break_type="Break of Structure",
        direction="bullish",
        level=104.2,
        confidence=0.71,
        source_event_type=str(CANDLE_CLOSED),
    ).to_payload()
    character_payload = CharacterChangedPayload(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=timestamp,
        previous_character="Trend Structure",
        character="Counter Trend Structure",
        confidence=0.64,
        source_event_type=str(CANDLE_CLOSED),
    ).to_payload()
    liquidity_payload = LiquidityDetectedPayload(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=timestamp,
        liquidity_type="Liquidity Sweep",
        side="buy_side",
        level=102.0,
        confidence=0.68,
        source_event_type=str(CANDLE_CLOSED),
    ).to_payload()

    assert break_payload["break_type"] == "Break of Structure"
    assert character_payload["character"] == "Counter Trend Structure"
    assert liquidity_payload["liquidity_type"] == "Liquidity Sweep"


def test_market_structure_plugin_metadata_and_subscriptions() -> None:
    plugin = MarketStructureEnginePlugin()

    assert plugin.metadata.plugin_id == "market_structure_engine"
    assert plugin.metadata.depends_on == frozenset({"feature_engine", "indicator_engine"})
    assert set(plugin.subscriptions()) == set(SUBSCRIBED_EVENTS)


async def test_market_structure_service_detects_break_of_structure() -> None:
    repo, cache, aggregation = await _bundle()
    await _seed_bullish_structure(repo)
    service = MarketStructureService(
        repository=repo,
        indicator_cache=cache,
        aggregation_engine=aggregation,
        settings=MarketStructureSettings(lookback_bars=12, swing_window=1),
    )

    batch = await service.ingest_snapshot(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        source_event_type=str(CANDLE_CLOSED),
    )

    assert batch is not None
    assert "Break of Structure" in batch.evaluation.detected
    assert "Higher High" in batch.evaluation.detected
    assert batch.structure_break is not None
    assert batch.updated["structure"] in {"Bull Structure", "Breakout Structure"}
    assert batch.updated["confidence"] >= 0.5


async def test_market_structure_service_detects_liquidity_sweep() -> None:
    repo, cache, aggregation = await _bundle()
    await _seed_liquidity_sweep(repo)
    service = MarketStructureService(
        repository=repo,
        indicator_cache=cache,
        aggregation_engine=aggregation,
        settings=MarketStructureSettings(lookback_bars=12, swing_window=1),
    )

    batch = await service.ingest_snapshot(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        source_event_type=str(CANDLE_CLOSED),
    )

    assert batch is not None
    assert batch.liquidity_detected is not None
    assert any(label in batch.evaluation.detected for label in ("Liquidity Grab", "Liquidity Sweep"))


async def test_market_structure_plugin_publishes_transport_events() -> None:
    repo, cache, aggregation = await _bundle()
    await _seed_bullish_structure(repo)
    plugin = MarketStructureEnginePlugin()
    install_context = _InstallContext()
    await plugin.install(install_context)

    context = _CapturingContext(
        {
            MarketDataRepository: repo,
            IndicatorCache: cache,
            TimeframeAggregationEngine: aggregation,
        }
    )
    await plugin.configure(context)
    await plugin.start(context)

    event = EventEnvelope.create(
        source="market_data",
        event_type=CANDLE_CLOSED,
        payload={
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "timeframe": "1m",
            "observed_at": _now(5).isoformat(),
            "open_price": 102.4,
            "high_price": 104.5,
            "low_price": 102.0,
            "close_price": 103.9,
            "volume": 1_400.0,
            "trades": 16,
        },
    )
    await plugin.handle_candle_closed(event)

    await plugin.stop(context)

    event_types = [envelope.event_type for envelope in context.published]
    assert MARKET_STRUCTURE_UPDATED in event_types
    assert STRUCTURE_BREAK in event_types
    assert all(envelope.source == "market_structure_engine" for envelope in context.published)


async def test_market_structure_plugin_updates_context_from_indicator_feature_and_regime_events() -> None:
    repo, cache, aggregation = await _bundle()
    plugin = MarketStructureEnginePlugin()
    await plugin.install(_InstallContext())
    context = _CapturingContext(
        {
            MarketDataRepository: repo,
            IndicatorCache: cache,
            TimeframeAggregationEngine: aggregation,
        }
    )
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
                "indicators": {"EMA": 103.5, "ADX": 27.0, "MACD": (1.2, 0.8, 0.4)},
                "versions": {"EMA": "1.0", "ADX": "1.0", "MACD": "1.0"},
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
                "observed_at": _now().isoformat(),
                "generated_at": _now().isoformat(),
                "source_event_type": str(CANDLE_CLOSED),
                "features": {"relative_volume": 1.3, "volume_ratio": 1.2},
            },
        )
    )
    await plugin.handle_pattern_detected(
        EventEnvelope.create(
            source="pattern_engine",
            event_type=PATTERN_DETECTED,
            payload={
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "observed_at": _now().isoformat(),
                "pattern": "Bullish Engulfing",
                "confidence": 0.74,
            },
        )
    )
    await plugin.handle_market_regime_changed(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=MARKET_REGIME_CHANGED,
            payload={
                "symbol": "AAPL",
                "timeframe": "1m",
                "observed_at": _now().isoformat(),
                "regime": "Bull Trend",
                "confidence": 0.81,
            },
        )
    )

    await plugin.stop(context)

    assert context.published == []
    assert plugin.metadata.plugin_id == "market_structure_engine"
    assert plugin.metadata.name == "Market Structure Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_market_structure_health_reflects_activity() -> None:
    repo, cache, aggregation = await _bundle()
    plugin = MarketStructureEnginePlugin()
    await plugin.install(_InstallContext())
    context = _CapturingContext(
        {
            MarketDataRepository: repo,
            IndicatorCache: cache,
            TimeframeAggregationEngine: aggregation,
        }
    )
    await plugin.configure(context)
    initial_health = await plugin.health_check()
    await plugin.start(context)
    running_health = await plugin.health_check()
    await plugin.stop(context)

    assert initial_health.status is HealthStatus.DEGRADED
    assert running_health.status is HealthStatus.DEGRADED
