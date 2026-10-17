from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugin_sdk.configuration import ConfigurationField, PluginConfigurationSpec
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED, FeatureSet
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED, IndicatorsUpdatedPayload
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    PUBLISHED_EVENTS,
    STRUCTURE_BREAK,
    SUBSCRIBED_EVENTS,
)
from tranalyset.plugins.market_structure_engine.service import (
    MarketStructureBatch,
    MarketStructureService,
    MarketStructureSettings,
)
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
)


class MarketStructureEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="market_structure_engine",
                name="Market Structure Engine",
                version="1.0.0",
                depends_on=frozenset({"feature_engine", "indicator_engine"}),
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("lookback_bars", int, required=False, default=60),
                    ConfigurationField("swing_window", int, required=False, default=2),
                    ConfigurationField("equality_tolerance", float, required=False, default=0.0015),
                    ConfigurationField("structure_break_threshold", float, required=False, default=0.002),
                    ConfigurationField("liquidity_sweep_threshold", float, required=False, default=0.001),
                    ConfigurationField("fvg_threshold", float, required=False, default=0.001),
                    ConfigurationField("min_confidence", float, required=False, default=0.5),
                )
            ),
        )
        self._service: MarketStructureService | None = None
        self._event_counts = {str(event_type): 0 for event_type in SUBSCRIBED_EVENTS}

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(MarketStructureEnginePlugin, self)
        context.register_service_factory(MarketStructureService, lambda _container: self._service, replace=True)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Market structure engine publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            msg = "Market structure engine has no runtime context."
            raise RuntimeError(msg)

        repository = self._context.resolve(MarketDataRepository)
        indicator_cache = self._context.resolve(IndicatorCache)
        aggregation_engine = self._context.resolve(TimeframeAggregationEngine)
        settings = MarketStructureSettings(
            lookback_bars=self.configuration.get("lookback_bars", int),
            swing_window=self.configuration.get("swing_window", int),
            equality_tolerance=self.configuration.get("equality_tolerance", float),
            structure_break_threshold=self.configuration.get("structure_break_threshold", float),
            liquidity_sweep_threshold=self.configuration.get("liquidity_sweep_threshold", float),
            fvg_threshold=self.configuration.get("fvg_threshold", float),
            min_confidence=self.configuration.get("min_confidence", float),
        )
        self._service = MarketStructureService(
            repository=repository,
            indicator_cache=indicator_cache,
            aggregation_engine=aggregation_engine,
            settings=settings,
        )

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_candle_event(event)

    @subscribes_to(TIMEFRAME_CANDLE_CLOSED)
    async def handle_timeframe_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_candle_event(event)

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        payload = _indicator_payload(event)
        await service.update_indicator_context(payload)

    @subscribes_to(FEATURES_UPDATED)
    async def handle_features_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        feature_set = _feature_set(event)
        await service.update_feature_context(feature_set)

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_pattern_event(event, valid=True)

    @subscribes_to(PATTERN_UPDATED)
    async def handle_pattern_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_pattern_event(event, valid=True)

    @subscribes_to(PATTERN_INVALIDATED)
    async def handle_pattern_invalidated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._handle_pattern_event(event, valid=False)

    @subscribes_to(MARKET_REGIME_CHANGED)
    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        symbol = _text(event.payload, "symbol", default="")
        timeframe = _timeframe(event.payload, default=Timeframe.ONE_MINUTE)
        regime = _text(event.payload, "regime", default="")
        confidence = _number(event.payload, "confidence", default=0.0)
        if symbol and regime:
            await service.update_market_regime(
                symbol=symbol,
                exchange=_text(event.payload, "exchange", default=_text(event.payload, "venue", default="UNKNOWN")),
                timeframe=timeframe,
                regime=regime,
                confidence=confidence,
                observed_at=_timestamp(event.payload, default=event.timestamp),
                source_event_type=str(event.event_type),
            )

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._service is None or self._service.evaluations == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Market structure engine is running before any structural evaluations.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Market structure engine is evaluating structure across timeframes.",
            details={**self._event_counts, "evaluations": self._service.evaluations},
        )

    async def _handle_candle_event(self, event: EventEnvelope) -> None:
        service = self._ensure_service()
        candle = _candle(event)
        batch = await service.ingest_candle(candle, source_event_type=str(event.event_type))
        if batch is not None:
            await self._publish_batch(batch)

    async def _handle_pattern_event(self, event: EventEnvelope, *, valid: bool) -> None:
        service = self._ensure_service()
        symbol = _text(event.payload, "symbol", default="")
        exchange = _text(event.payload, "exchange", default=_text(event.payload, "venue", default="UNKNOWN"))
        timeframe = _timeframe(event.payload, default=Timeframe.ONE_MINUTE)
        pattern = _text(event.payload, "pattern", default=None)
        confidence = _number(event.payload, "confidence", default=0.0)
        if symbol:
            await service.update_pattern_context(
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                observed_at=_timestamp(event.payload, default=event.timestamp),
                pattern=pattern,
                confidence=confidence,
                source_event_type=str(event.event_type),
                valid=valid,
            )

    async def _publish_batch(self, batch: MarketStructureBatch) -> None:
        await self.publish(MARKET_STRUCTURE_UPDATED, payload=batch.updated)
        if batch.structure_break is not None:
            await self.publish(STRUCTURE_BREAK, payload=batch.structure_break)
        if batch.character_changed is not None:
            await self.publish(CHARACTER_CHANGED, payload=batch.character_changed)
        if batch.liquidity_detected is not None:
            await self.publish(LIQUIDITY_DETECTED, payload=batch.liquidity_detected)

    def _ensure_service(self) -> MarketStructureService:
        if self._service is None:
            msg = "Market structure service is not configured."
            raise RuntimeError(msg)
        return self._service

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def _candle(event: EventEnvelope) -> Candle:
    payload = event.payload
    symbol = _text(payload, "symbol", default="")
    exchange = _text(payload, "exchange", default=_text(payload, "venue", default="UNKNOWN"))
    timeframe = _timeframe(payload, default=Timeframe.ONE_MINUTE)
    observed_at = _timestamp(
        payload,
        default=event.timestamp,
        keys=("opened_at", "observed_at", "closed_at", "timestamp", "received_at", "generated_at"),
    )
    open_price = _number(payload, "open_price", default=_number(payload, "open", default=0.0))
    high_price = _number(payload, "high_price", default=_number(payload, "high", default=open_price))
    low_price = _number(payload, "low_price", default=_number(payload, "low", default=open_price))
    close_price = _number(payload, "close_price", default=_number(payload, "close", default=open_price))
    volume = _number(payload, "volume", default=_number(payload, "size", default=0.0))
    trades = int(_number(payload, "trades", default=0.0))
    return Candle(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        timestamp=observed_at,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        trades=trades,
    )


def _indicator_payload(event: EventEnvelope) -> IndicatorsUpdatedPayload:
    payload = event.payload
    return IndicatorsUpdatedPayload(
        symbol=_text(payload, "symbol", default=""),
        exchange=_text(payload, "exchange", default=_text(payload, "venue", default="UNKNOWN")),
        timeframe=_timeframe(payload, default=Timeframe.ONE_MINUTE).value,
        observed_at=_timestamp(payload, default=event.timestamp),
        indicators=dict(payload.get("indicators", {})),
        versions=dict(payload.get("versions", {})),
        source_event_type=str(event.event_type),
    )


def _feature_set(event: EventEnvelope) -> FeatureSet:
    payload = event.payload
    observed_at = _timestamp(payload, default=event.timestamp)
    return FeatureSet(
        feature_set_id=_text(payload, "feature_set_id", default=f"feature-{event.timestamp.timestamp():.0f}"),
        symbol=_text(payload, "symbol", default=""),
        exchange=_text(payload, "exchange", default=_text(payload, "venue", default="UNKNOWN")),
        timeframe=_timeframe(payload, default=Timeframe.ONE_MINUTE).value,
        observed_at=observed_at,
        source_event_type=_text(payload, "source_event_type", default=str(event.event_type)),
        features={key: float(value) for key, value in dict(payload.get("features", {})).items() if isinstance(value, (int, float))},
        metadata=dict(payload.get("metadata", {})),
        generated_at=observed_at,
    )


def _text(payload: dict[str, Any], *keys: str, default: str | None) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return default or ""


def _number(payload: dict[str, Any], key: str, *, default: float) -> float:
    value = payload.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _timestamp(
    payload: dict[str, Any],
    *,
    default: datetime,
    keys: tuple[str, ...] = ("observed_at", "closed_at", "timestamp", "received_at", "generated_at"),
) -> datetime:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return default.astimezone(UTC)


def _timeframe(payload: dict[str, Any], *, default: Timeframe) -> Timeframe:
    value = payload.get("timeframe")
    if isinstance(value, str) and value:
        return Timeframe(value)
    return default


def create_plugin() -> MarketStructureEnginePlugin:
    return MarketStructureEnginePlugin()


__all__ = [
    "MarketStructureEnginePlugin",
    "create_plugin",
]
