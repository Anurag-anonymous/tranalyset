from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugin_sdk.configuration import ConfigurationField, PluginConfigurationSpec
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED
from tranalyset.plugins.feature_engine.service import FeatureEngineeringService
from tranalyset.plugins.indicator_engine.contracts import (
    INDICATOR_CALCULATED,
    INDICATOR_INVALIDATED,
    INDICATORS_UPDATED,
    PUBLISHED_EVENTS,
    SUBSCRIBED_EVENTS,
    IndicatorCalculatedPayload,
    IndicatorInvalidatedPayload,
    IndicatorsUpdatedPayload,
)
from tranalyset.plugins.indicator_engine.service import (
    IndicatorBatch,
    IndicatorEngineService,
    IndicatorEngineSettings,
    IndicatorObservation,
)
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED, TIMEFRAME_CANDLE_CLOSED


class IndicatorEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="indicator_engine",
                name="Indicator Engine",
                version="1.0.0",
                depends_on=frozenset({"feature_engine"}),
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("history_limit", int, required=False, default=180),
                    ConfigurationField("ema_fast", int, required=False, default=12),
                    ConfigurationField("ema_slow", int, required=False, default=26),
                    ConfigurationField("ema_signal", int, required=False, default=9),
                    ConfigurationField("sma_period", int, required=False, default=20),
                    ConfigurationField("rsi_period", int, required=False, default=14),
                    ConfigurationField("atr_period", int, required=False, default=14),
                    ConfigurationField("adx_period", int, required=False, default=14),
                    ConfigurationField("volume_profile_window", int, required=False, default=50),
                )
            ),
        )
        self._service: IndicatorEngineService | None = None
        self._feature_engine: FeatureEngineeringService | None = None
        self._event_counts = {str(event_type): 0 for event_type in SUBSCRIBED_EVENTS}

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(IndicatorEnginePlugin, self)
        context.register_service_factory(IndicatorEngineService, lambda _container: self._service, replace=True)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Indicator engine publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            msg = "Indicator engine has no runtime context."
            raise RuntimeError(msg)

        repository = self._context.resolve(MarketDataRepository)
        indicator_cache = self._context.resolve(IndicatorCache)
        aggregation_engine = self._context.resolve(TimeframeAggregationEngine)
        feature_engine = self._context.resolve(FeatureEngineeringService)
        self._feature_engine = feature_engine if isinstance(feature_engine, FeatureEngineeringService) else None

        settings = IndicatorEngineSettings(
            history_limit=self.configuration.get("history_limit", int),
            ema_fast=self.configuration.get("ema_fast", int),
            ema_slow=self.configuration.get("ema_slow", int),
            ema_signal=self.configuration.get("ema_signal", int),
            sma_period=self.configuration.get("sma_period", int),
            rsi_period=self.configuration.get("rsi_period", int),
            atr_period=self.configuration.get("atr_period", int),
            adx_period=self.configuration.get("adx_period", int),
            volume_profile_window=self.configuration.get("volume_profile_window", int),
        )
        self._service = IndicatorEngineService(
            repository=repository,
            indicator_cache=indicator_cache,
            aggregation_engine=aggregation_engine,
            feature_engine=self._feature_engine,
            settings=settings,
        )

    @subscribes_to(TICK_RECEIVED)
    async def handle_tick_received(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_market_event(event, default_timeframe=self._service.settings.tick_timeframe if self._service else Timeframe.ONE_MINUTE)

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_market_event(event, default_timeframe=None)

    @subscribes_to(TIMEFRAME_CANDLE_CLOSED)
    async def handle_timeframe_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_market_event(event, default_timeframe=None)

    @subscribes_to(FEATURES_UPDATED)
    async def handle_features_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        payload = event.payload
        symbol = _text(payload, "symbol", default="")
        exchange = _text(payload, "exchange", default="")
        timeframe_value = _text(payload, "timeframe", default="")
        feature_set_id = _text(payload, "feature_set_id", default="")
        features = payload.get("features", {})
        feature_count = len(features) if isinstance(features, Mapping) else 0
        if symbol and exchange and timeframe_value and feature_set_id:
            await service.ingest_feature_context(
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe_value,
                feature_set_id=feature_set_id,
                feature_count=feature_count,
                source_event_type=str(event.event_type),
            )

    async def publish_indicator_calculated(self, payload: IndicatorCalculatedPayload) -> None:
        await self.publish(INDICATOR_CALCULATED, payload=payload.to_payload())

    async def publish_indicator_invalidated(self, payload: IndicatorInvalidatedPayload) -> None:
        await self.publish(INDICATOR_INVALIDATED, payload=payload.to_payload())

    async def publish_indicators_updated(self, payload: IndicatorsUpdatedPayload) -> None:
        await self.publish(INDICATORS_UPDATED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._service is None or self._service.update_count == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Indicator engine is running before any streaming updates.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Indicator engine is calculating indicators incrementally.",
            details={
                **self._event_counts,
                "updates": self._service.update_count,
                "bootstraps": self._service.bootstrap_count,
            },
        )

    async def _handle_market_event(self, event: EventEnvelope, *, default_timeframe: Timeframe | None) -> None:
        service = self._ensure_service()
        observation = _observation_from_event(event, default_timeframe=default_timeframe or service.settings.tick_timeframe)
        batch = await service.ingest(observation)
        await self._publish_batch(batch)

    async def _publish_batch(self, batch: IndicatorBatch) -> None:
        for indicator in batch.invalidated:
            await self.publish_indicator_invalidated(
                IndicatorInvalidatedPayload(
                    symbol=batch.symbol,
                    exchange=batch.exchange,
                    timeframe=batch.timeframe,
                    observed_at=batch.observed_at,
                    indicator=indicator,
                    reason="state_reset",
                    source_event_type=batch.source_event_type,
                    previous_version=batch.versions.get(indicator),
                    metadata=batch.metadata,
                )
            )

        for indicator, value in batch.indicators.items():
            await self.publish_indicator_calculated(
                IndicatorCalculatedPayload(
                    symbol=batch.symbol,
                    exchange=batch.exchange,
                    timeframe=batch.timeframe,
                    observed_at=batch.observed_at,
                    indicator=indicator,
                    value=value,
                    version=batch.versions[indicator],
                    source_event_type=batch.source_event_type,
                    dependencies=tuple(batch.dependencies),
                    metadata=batch.metadata,
                )
            )

        await self.publish_indicators_updated(
            IndicatorsUpdatedPayload(
                symbol=batch.symbol,
                exchange=batch.exchange,
                timeframe=batch.timeframe,
                observed_at=batch.observed_at,
                indicators=batch.indicators,
                versions=batch.versions,
                source_event_type=batch.source_event_type,
                metadata=batch.metadata,
            )
        )

    def _ensure_service(self) -> IndicatorEngineService:
        if self._service is None:
            msg = "Indicator engine service is not configured."
            raise RuntimeError(msg)
        return self._service

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def _observation_from_event(
    event: EventEnvelope,
    *,
    default_timeframe: Timeframe,
) -> IndicatorObservation:
    payload = event.payload
    symbol = _text(payload, "symbol", default="")
    exchange = _text(payload, "exchange", default=_text(payload, "venue", default="UNKNOWN"))
    timeframe = _timeframe(payload, default=default_timeframe)
    observed_at = _timestamp(payload)
    if event.event_type == TICK_RECEIVED:
        price = _number(payload, ("price", "close_price", "close"), default=0.0)
        volume = _number(payload, ("size", "volume"), default=0.0)
        return IndicatorObservation(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            observed_at=observed_at,
            source_event_type=str(event.event_type),
            open_price=price,
            high_price=price,
            low_price=price,
            close_price=price,
            volume=volume,
        )

    open_price = _number(payload, ("open_price", "open"), default=0.0)
    high_price = _number(payload, ("high_price", "high"), default=open_price)
    low_price = _number(payload, ("low_price", "low"), default=open_price)
    close_price = _number(payload, ("close_price", "close"), default=open_price)
    volume = _number(payload, ("volume", "size"), default=0.0)
    return IndicatorObservation(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        observed_at=observed_at,
        source_event_type=str(event.event_type),
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
    )


def _text(payload: dict[str, Any], *keys: str, default: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return default


def _number(payload: dict[str, Any], keys: tuple[str, ...], *, default: float) -> float:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return default


def _timestamp(payload: dict[str, Any]) -> datetime:
    for key in ("observed_at", "closed_at", "received_at", "timestamp", "opened_at"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return datetime.now(UTC)


def _timeframe(payload: dict[str, Any], *, default: Timeframe) -> Timeframe:
    value = payload.get("timeframe")
    if isinstance(value, str) and value:
        return Timeframe(value)
    return default


def create_plugin() -> IndicatorEnginePlugin:
    return IndicatorEnginePlugin()


__all__ = [
    "IndicatorEnginePlugin",
    "create_plugin",
]
