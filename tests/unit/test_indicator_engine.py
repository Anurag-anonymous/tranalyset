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
from tranalyset.plugins.feature_engine import FeatureEngineeringService, FeatureEngineeringSettings, FeatureObservation
from tranalyset.plugins.indicator_engine import (
    INDICATOR_CALCULATED,
    INDICATOR_INVALIDATED,
    INDICATORS_UPDATED,
    IndicatorEnginePlugin,
    IndicatorEngineService,
    IndicatorEngineSettings,
    IndicatorObservation,
    PUBLISHED_EVENTS,
    SUPPORTED_INDICATORS,
    SUBSCRIBED_EVENTS,
)
from tranalyset.plugins.indicator_engine.contracts import IndicatorCalculatedPayload, IndicatorInvalidatedPayload
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED as TECH_INDICATORS_UPDATED


class _CapturingContext:
    def __init__(self, services: Mapping[object, object], *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "indicator_engine"
        self.config = config or {}
        self._services = dict(services)
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return self._services[key]


class _InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "indicator_engine"
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
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC) + timedelta(minutes=offset_minutes)


async def _seed_repo(repo: MarketDataRepository, *, count: int = 60) -> None:
    for index in range(count):
        ts = _now(index)
        close = 100.0 + (index * 0.5)
        await repo.append_candle(
            Candle(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=ts,
                open=close - 0.25,
                high=close + 0.75,
                low=close - 0.85,
                close=close,
                volume=1_000.0 + (index * 20.0),
                trades=10 + index,
            )
        )


async def _feature_engine_bundle() -> tuple[MarketDataRepository, IndicatorCache, TimeframeAggregationEngine, FeatureEngineeringService]:
    backend = InMemoryStorageBackend()
    repo = MarketDataRepository(backend)
    cache = IndicatorCache(repository=repo, event_bus=object())  # type: ignore[arg-type]
    aggregation = TimeframeAggregationEngine(repository=repo, event_bus=object())  # type: ignore[arg-type]
    feature_engine = FeatureEngineeringService(
        repository=repo,
        indicator_cache=cache,
        aggregation_engine=aggregation,
        settings=FeatureEngineeringSettings(),
    )
    return repo, cache, aggregation, feature_engine


def _observation(*, source_event_type: str, offset_minutes: int = 60, timeframe: Timeframe = Timeframe.ONE_MINUTE) -> IndicatorObservation:
    ts = _now(offset_minutes)
    close = 130.0 + offset_minutes * 0.25
    return IndicatorObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=timeframe,
        observed_at=ts,
        source_event_type=source_event_type,
        open_price=close - 0.5,
        high_price=close + 1.0,
        low_price=close - 1.25,
        close_price=close,
        volume=1_500.0 + offset_minutes * 10.0,
    )


def test_indicator_engine_plugin_metadata_and_contracts() -> None:
    plugin = IndicatorEnginePlugin()

    assert plugin.metadata.plugin_id == "indicator_engine"
    assert plugin.metadata.depends_on == frozenset({"feature_engine"})
    assert set(plugin.subscriptions()) == set(SUBSCRIBED_EVENTS)


async def test_indicator_engine_install_registers_events() -> None:
    plugin = IndicatorEnginePlugin()
    install_context = _InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS
    assert IndicatorEnginePlugin in install_context.services


async def test_indicator_engine_service_calculates_all_supported_indicators() -> None:
    repo, cache, aggregation, feature_engine = await _feature_engine_bundle()
    await _seed_repo(repo)
    feature_observation = FeatureObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        observed_at=_now(60),
        source_event_type=str(CANDLE_CLOSED),
        open_price=130.0,
        high_price=131.0,
        low_price=128.5,
        close_price=130.75,
        volume=2_000.0,
    )
    feature_set = await feature_engine.generate(feature_observation)
    service = IndicatorEngineService(
        repository=repo,
        indicator_cache=cache,
        aggregation_engine=aggregation,
        feature_engine=feature_engine,
        settings=IndicatorEngineSettings(),
    )

    batch = await service.ingest(
        _observation(source_event_type=str(CANDLE_CLOSED), offset_minutes=60)
    )

    assert set(batch.indicators) == set(SUPPORTED_INDICATORS)
    assert batch.metadata["feature_set_id"] == feature_set.feature_set_id
    assert batch.invalidated == ()
    assert batch.indicators["MACD"]
    assert batch.indicators["Bollinger"]
    assert batch.indicators["VolumeProfile"]
    cached_ema = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", period=26)
    cached_macd = await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "MACD", period=26)
    assert cached_ema is not None
    assert cached_macd is not None
    assert service.bootstrap_count == 1
    assert service.update_count == 1


async def test_indicator_engine_invalidation_resets_state() -> None:
    repo, cache, aggregation, feature_engine = await _feature_engine_bundle()
    await _seed_repo(repo)
    service = IndicatorEngineService(
        repository=repo,
        indicator_cache=cache,
        aggregation_engine=aggregation,
        feature_engine=feature_engine,
        settings=IndicatorEngineSettings(),
    )

    await service.ingest(_observation(source_event_type=str(CANDLE_CLOSED), offset_minutes=60))
    invalidated = await service.invalidate(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        reason="manual_reset",
        source_event_type="manual",
    )

    assert "EMA" in invalidated
    assert await cache.get("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", period=26) is None


async def test_indicator_engine_plugin_publishes_events_and_uses_feature_context() -> None:
    repo, cache, aggregation, feature_engine = await _feature_engine_bundle()
    await _seed_repo(repo)
    feature_observation = FeatureObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        observed_at=_now(60),
        source_event_type=str(CANDLE_CLOSED),
        open_price=130.0,
        high_price=131.0,
        low_price=128.5,
        close_price=130.75,
        volume=2_000.0,
    )
    feature_set = await feature_engine.generate(feature_observation)
    context = _CapturingContext(
        {
            MarketDataRepository: repo,
            IndicatorCache: cache,
            TimeframeAggregationEngine: aggregation,
            FeatureEngineeringService: feature_engine,
        }
    )
    plugin = IndicatorEnginePlugin()

    await plugin.install(_InstallContext())
    await plugin.configure(context)
    await plugin.start(context)

    await plugin.handle_candle_closed(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "closed_at": _now(61).isoformat(),
                "open_price": 131.0,
                "high_price": 132.5,
                "low_price": 130.5,
                "close_price": 131.25,
                "volume": 2_100.0,
            },
        )
    )

    assert context.published
    assert any(event.event_type == INDICATORS_UPDATED for event in context.published)
    assert any(event.event_type == INDICATOR_CALCULATED for event in context.published)
    assert context.published[-1].payload["metadata"]["feature_set_id"] == feature_set.feature_set_id
    health = await plugin.health_check()
    await plugin.stop(context)

    assert health.status is HealthStatus.HEALTHY


async def test_indicator_engine_publishes_invalidation_for_out_of_order_events() -> None:
    repo, cache, aggregation, feature_engine = await _feature_engine_bundle()
    await _seed_repo(repo)
    context = _CapturingContext(
        {
            MarketDataRepository: repo,
            IndicatorCache: cache,
            TimeframeAggregationEngine: aggregation,
            FeatureEngineeringService: feature_engine,
        }
    )
    plugin = IndicatorEnginePlugin()

    await plugin.install(_InstallContext())
    await plugin.configure(context)
    await plugin.start(context)

    await plugin.handle_candle_closed(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "closed_at": _now(62).isoformat(),
                "open_price": 131.0,
                "high_price": 132.5,
                "low_price": 130.5,
                "close_price": 131.25,
                "volume": 2_100.0,
            },
        )
    )
    await plugin.handle_candle_closed(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "closed_at": _now(61).isoformat(),
                "open_price": 130.0,
                "high_price": 131.0,
                "low_price": 129.5,
                "close_price": 130.25,
                "volume": 1_900.0,
            },
        )
    )

    assert any(event.event_type == INDICATOR_INVALIDATED for event in context.published)
    await plugin.stop(context)


def test_indicator_payloads_support_complex_values() -> None:
    payload = IndicatorCalculatedPayload(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=_now(),
        indicator="MACD",
        value=(1.0, 0.5, 0.5),
        version="1.0.0:macd:12:26:9",
        source_event_type=str(TICK_RECEIVED),
        dependencies=("EMA",),
        metadata={"engine_version": "1.0.0"},
    ).to_payload()

    assert payload["value"] == (1.0, 0.5, 0.5)
    invalidated = IndicatorInvalidatedPayload(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=_now(),
        indicator="EMA",
        reason="reset",
        source_event_type="manual",
    ).to_payload()

    assert invalidated["reason"] == "reset"
    assert TECH_INDICATORS_UPDATED == INDICATORS_UPDATED
