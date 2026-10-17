from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from math import isclose

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, MarketCalendar, MarketSession, Tick, Timeframe, TradingSession
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.feature_engine import (
    FEATURES_UPDATED,
    FeatureEngineeringPlugin,
    FeatureEngineeringService,
    FeatureEngineeringSettings,
    FeatureObservation,
    FeatureSet,
    TIMEFRAME_CANDLE_CLOSED,
)
from tranalyset.plugins.feature_engine.contracts import PUBLISHED_EVENTS, SUBSCRIBED_EVENTS
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED


class _CapturingContext:
    def __init__(self, services: Mapping[object, object], *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "feature_engine"
        self.config = config or {}
        self._services = dict(services)
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return self._services[key]


class _InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "feature_engine"
        self.events: list[object] = []

    def register_event(self, event_type: object, *, description: str, replace: bool = False) -> None:
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
    return datetime(2026, 1, 1, 9, 31, tzinfo=UTC)


async def _build_services() -> tuple[MarketDataRepository, IndicatorCache, TimeframeAggregationEngine]:
    backend = InMemoryStorageBackend()
    repo = MarketDataRepository(backend)
    bus = AsyncEventBus()
    aggregation = TimeframeAggregationEngine(repository=repo, event_bus=bus)
    indicator_cache = IndicatorCache(repository=repo, event_bus=bus)
    return repo, indicator_cache, aggregation


async def _seed_history(repo: MarketDataRepository) -> None:
    await repo.append_calendar(
        MarketCalendar(
            exchange="NASDAQ",
            timezone="UTC",
            sessions=(
                TradingSession(
                    exchange="NASDAQ",
                    session_type=MarketSession.REGULAR,
                    start=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
                    end=datetime(2026, 1, 1, 16, 0, tzinfo=UTC),
                ),
            ),
        )
    )
    for offset, close in enumerate((100.0, 100.5, 101.0)):
        ts = datetime(2026, 1, 1, 9, 30 + offset, tzinfo=UTC)
        await repo.append_candle(
            Candle(
                symbol="AAPL",
                exchange="NASDAQ",
                timeframe=Timeframe.ONE_MINUTE,
                timestamp=ts,
                open=close - 0.25,
                high=close + 0.5,
                low=close - 0.75,
                close=close,
                volume=1_000.0 + (offset * 100.0),
                trades=10 + offset,
            )
        )


def test_feature_set_is_immutable_and_hashable() -> None:
    feature_set = FeatureSet(
        feature_set_id="AAPL:NASDAQ:1m:TickReceived:2026-01-01T09:31:00+00:00",
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=_now(),
        source_event_type="TickReceived",
        features={"price_change": 1.0, "volume_ratio": 1.2},
        metadata={"window": 20},
        generated_at=_now(),
    )

    assert feature_set.features["price_change"] == 1.0
    assert hash(feature_set)
    assert feature_set.to_payload()["generated_at"].endswith("+00:00")

    with pytest.raises(TypeError):
        feature_set.features["price_change"] = 2.0  # type: ignore[index]


@pytest.mark.asyncio
async def test_feature_service_generates_candle_features_and_caches_them() -> None:
    repo, indicator_cache, aggregation = await _build_services()
    await _seed_history(repo)
    current = _now()
    await indicator_cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "EMA", current, 101.0, period=20)
    await indicator_cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "SMA", current, 100.75, period=20)
    await indicator_cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "VWAP", current, 100.8, period=20)
    await indicator_cache.put("AAPL", "NASDAQ", Timeframe.ONE_MINUTE, "ATR", current, 1.25, period=14)

    service = FeatureEngineeringService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
        settings=FeatureEngineeringSettings(),
    )
    observation = FeatureObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        observed_at=current,
        source_event_type=str(CANDLE_CLOSED),
        open_price=101.0,
        high_price=102.0,
        low_price=100.0,
        close_price=101.5,
        volume=1_300.0,
    )

    feature_set = await service.generate(observation)

    assert feature_set.symbol == "AAPL"
    assert feature_set.exchange == "NASDAQ"
    assert feature_set.features["price_change"] == 0.5
    assert isclose(feature_set.features["log_returns"], 0.004938, rel_tol=1e-3)
    assert feature_set.features["gap_size"] == 1.0
    assert feature_set.features["session_position"] > 0.0
    assert feature_set.features["vwap_distance"] != 0.0
    assert await service.latest(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        source_event_type=str(CANDLE_CLOSED),
    ) == feature_set
    assert len(await service.history(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        source_event_type=str(CANDLE_CLOSED),
    )) == 1


@pytest.mark.asyncio
async def test_feature_service_uses_previous_market_price_for_tick_features() -> None:
    repo, indicator_cache, aggregation = await _build_services()
    await repo.append_candle(
        Candle(
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe=Timeframe.ONE_MINUTE,
            timestamp=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1_000.0,
            trades=10,
        )
    )

    service = FeatureEngineeringService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
        settings=FeatureEngineeringSettings(),
    )
    observation = FeatureObservation(
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe=Timeframe.ONE_MINUTE,
        observed_at=_now(),
        source_event_type=str(TICK_RECEIVED),
        open_price=101.0,
        high_price=101.0,
        low_price=101.0,
        close_price=101.0,
        volume=25.0,
    )

    feature_set = await service.generate(observation)

    assert feature_set.features["price_change"] == 1.0
    assert feature_set.features["gap_size"] == 1.0
    assert feature_set.features["gap_direction"] == 1.0
    assert feature_set.features["volume_ratio"] > 0.0


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_feature_service_handles_zero_volume_without_inventing_activity() -> None:
    repo, indicator_cache, aggregation = await _build_services()
    service = FeatureEngineeringService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
    )
    observation = FeatureObservation(
        symbol="NIFTY",
        exchange="NSE",
        timeframe=Timeframe.FIVE_MINUTES,
        observed_at=_now(),
        source_event_type=str(CANDLE_CLOSED),
        open_price=100.0,
        high_price=102.0,
        low_price=99.0,
        close_price=101.0,
        volume=0.0,
    )

    feature_set = await service.generate(observation)

    assert feature_set.features["relative_volume"] == 0.0
    assert feature_set.features["volume_ratio"] == 0.0


async def test_feature_service_does_not_use_future_repository_data() -> None:
    repo, indicator_cache, aggregation = await _build_services()
    await repo.append_tick(
        Tick(
            symbol="AAPL", exchange="NASDAQ", timestamp=datetime(2026, 1, 1, 9, 30, tzinfo=UTC), price=100.0, size=1.0
        )
    )
    await repo.append_tick(
        Tick(
            symbol="AAPL", exchange="NASDAQ", timestamp=datetime(2026, 1, 1, 9, 32, tzinfo=UTC), price=200.0, size=1.0
        )
    )
    service = FeatureEngineeringService(
        repository=repo,
        indicator_cache=indicator_cache,
        aggregation_engine=aggregation,
    )

    features = await service.generate(
        FeatureObservation(
            symbol="AAPL", exchange="NASDAQ", timeframe=Timeframe.ONE_MINUTE,
            observed_at=datetime(2026, 1, 1, 9, 31, tzinfo=UTC), source_event_type=str(TICK_RECEIVED),
            open_price=101.0, high_price=101.0, low_price=101.0, close_price=101.0, volume=1.0,
        )
    )

    assert features.features["price_change"] == 1.0


@pytest.mark.asyncio
async def test_feature_plugin_installs_subscriptions_and_publishes_features() -> None:
    repo, indicator_cache, aggregation = await _build_services()
    await _seed_history(repo)
    context = _CapturingContext(
        {
            MarketDataRepository: repo,
            IndicatorCache: indicator_cache,
            TimeframeAggregationEngine: aggregation,
        }
    )
    plugin = FeatureEngineeringPlugin()
    install_context = _InstallContext()

    await plugin.install(install_context)
    await plugin.configure(context)
    await plugin.start(context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS
    assert set(plugin.subscriptions()) == set(SUBSCRIBED_EVENTS)

    await plugin.handle_candle_closed(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "venue": "NASDAQ",
                "timeframe": "1m",
                "closed_at": _now().isoformat(),
                "open_price": 101.0,
                "high_price": 102.0,
                "low_price": 100.0,
                "close_price": 101.5,
                "volume": 1_300.0,
            },
        )
    )

    assert context.published
    published = context.published[-1]
    assert published.event_type == FEATURES_UPDATED
    assert published.payload["symbol"] == "AAPL"
    assert published.payload["source_event_type"] == str(CANDLE_CLOSED)
    assert published.payload["features"]["price_change"] == 0.5

    health = await plugin.health_check()
    await plugin.stop(context)

    assert health.status is HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_feature_plugin_handles_timeframe_candles() -> None:
    repo, indicator_cache, aggregation = await _build_services()
    await _seed_history(repo)
    context = _CapturingContext(
        {
            MarketDataRepository: repo,
            IndicatorCache: indicator_cache,
            TimeframeAggregationEngine: aggregation,
        }
    )
    plugin = FeatureEngineeringPlugin()
    await plugin.install(_InstallContext())
    await plugin.configure(context)
    await plugin.start(context)

    await plugin.handle_timeframe_candle_closed(
        EventEnvelope.create(
            source="timeframe_aggregator",
            event_type=TIMEFRAME_CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "5m",
                "timestamp": _now().isoformat(),
                "open": 101.0,
                "high": 102.5,
                "low": 100.5,
                "close": 102.0,
                "volume": 3_500.0,
            },
        )
    )

    assert context.published[-1].payload["timeframe"] == "5m"
    assert context.published[-1].payload["features"]["high_low_range"] == 2.0
    await plugin.stop(context)
