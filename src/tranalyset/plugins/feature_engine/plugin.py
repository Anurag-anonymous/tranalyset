from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.payloads import require_text
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugin_sdk.configuration import ConfigurationField, PluginConfigurationSpec
from tranalyset.plugins.feature_engine.contracts import (
    FEATURES_UPDATED,
    PUBLISHED_EVENTS,
    SUBSCRIBED_EVENTS,
    FeatureSet,
)
from tranalyset.plugins.feature_engine.service import FeatureEngineeringService
from tranalyset.plugins.feature_engine.service import (
    FeatureEngineeringSettings,
    FeatureObservation,
)
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED, TIMEFRAME_CANDLE_CLOSED


class FeatureEngineeringPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="feature_engine",
                name="Feature Engineering Engine",
                version="1.0.0",
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("history_limit", int, required=False, default=120),
                    ConfigurationField("rolling_window", int, required=False, default=20),
                    ConfigurationField("trend_window", int, required=False, default=20),
                    ConfigurationField("momentum_window", int, required=False, default=5),
                    ConfigurationField("support_window", int, required=False, default=20),
                    ConfigurationField("resistance_window", int, required=False, default=20),
                    ConfigurationField("liquidity_window", int, required=False, default=20),
                    ConfigurationField("atr_window", int, required=False, default=14),
                    ConfigurationField("cache_size", int, required=False, default=250),
                    ConfigurationField("tick_timeframe", str, required=False, default=Timeframe.ONE_MINUTE.value),
                )
            ),
        )
        self._service: FeatureEngineeringService | None = None
        self._event_counts = {str(event_type): 0 for event_type in SUBSCRIBED_EVENTS}

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(FeatureEngineeringPlugin, self)
        context.register_service_factory(
            FeatureEngineeringService,
            lambda _container: self._service,
            replace=True,
        )
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Feature engineering plugin publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            msg = "Feature engineering plugin has no runtime context."
            raise RuntimeError(msg)

        repository = self._context.resolve(MarketDataRepository)
        indicator_cache = self._context.resolve(IndicatorCache)
        aggregation_engine = self._context.resolve(TimeframeAggregationEngine)

        settings = FeatureEngineeringSettings(
            history_limit=self.configuration.get("history_limit", int),
            rolling_window=self.configuration.get("rolling_window", int),
            trend_window=self.configuration.get("trend_window", int),
            momentum_window=self.configuration.get("momentum_window", int),
            support_window=self.configuration.get("support_window", int),
            resistance_window=self.configuration.get("resistance_window", int),
            liquidity_window=self.configuration.get("liquidity_window", int),
            atr_window=self.configuration.get("atr_window", int),
            cache_size=self.configuration.get("cache_size", int),
            tick_timeframe=Timeframe(self.configuration.get("tick_timeframe", str)),
        )
        self._service = FeatureEngineeringService(
            repository=repository,
            indicator_cache=indicator_cache,
            aggregation_engine=aggregation_engine,
            settings=settings,
        )

    @subscribes_to(TICK_RECEIVED)
    async def handle_tick_received(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        await self._handle_event(event, default_timeframe=service.settings.tick_timeframe)

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_event(event, default_timeframe=None)

    @subscribes_to(TIMEFRAME_CANDLE_CLOSED)
    async def handle_timeframe_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_event(event, default_timeframe=None)

    async def publish_features_updated(self, feature_set: FeatureSet) -> None:
        await self.publish(FEATURES_UPDATED, payload=feature_set.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._service is None or self._service.generated_count == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Feature engineering engine is ready but has not produced features yet.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Feature engineering engine is generating reusable features.",
            details={
                **self._event_counts,
                "generated_features": self._service.generated_count,
            },
        )

    async def _handle_event(self, event: EventEnvelope, *, default_timeframe: Timeframe | None) -> None:
        service = self._ensure_service()
        observation = self._observation_from_event(event, default_timeframe=default_timeframe, service=service)
        feature_set = await service.generate(observation)
        await self.publish_features_updated(feature_set)

    def _observation_from_event(
        self,
        event: EventEnvelope,
        *,
        default_timeframe: Timeframe | None,
        service: FeatureEngineeringService,
    ) -> FeatureObservation:
        payload = event.payload
        symbol = _text(payload, "symbol", default="")
        require_text(symbol, "symbol")
        exchange = _text(payload, "exchange", default=_text(payload, "venue", default="UNKNOWN"))
        require_text(exchange, "exchange")
        timeframe = _timeframe(payload, default_timeframe=default_timeframe or service.settings.tick_timeframe)
        observed_at = _timestamp(payload, default=event.timestamp)
        open_price = _number(
            payload,
            ("open_price", "open", "price"),
            default=_number(payload, ("close_price", "close", "price"), default=0.0),
        )
        high_price = _number(payload, ("high_price", "high", "price"), default=open_price)
        low_price = _number(payload, ("low_price", "low", "price"), default=open_price)
        close_price = _number(payload, ("close_price", "close", "price"), default=open_price)
        volume = _number(payload, ("volume", "size"), default=0.0)
        if event.event_type == TICK_RECEIVED:
            price = _number(payload, ("price", "close_price", "close"), default=close_price)
            open_price = price
            high_price = price
            low_price = price
            close_price = price
        return FeatureObservation(
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

    def _ensure_service(self) -> FeatureEngineeringService:
        if self._service is None:
            msg = "Feature engineering service is not configured."
            raise RuntimeError(msg)
        return self._service

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


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


def _timestamp(payload: dict[str, Any], *, default: datetime) -> datetime:
    for key in ("observed_at", "closed_at", "received_at", "timestamp", "opened_at"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return default.astimezone(UTC)


def _timeframe(payload: dict[str, Any], *, default_timeframe: Timeframe) -> Timeframe:
    value = payload.get("timeframe")
    if isinstance(value, str) and value:
        return Timeframe(value)
    return default_timeframe


def create_plugin() -> FeatureEngineeringPlugin:
    return FeatureEngineeringPlugin()


__all__ = [
    "FeatureEngineeringPlugin",
    "create_plugin",
]
