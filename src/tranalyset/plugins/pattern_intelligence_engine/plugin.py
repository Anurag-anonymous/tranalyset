from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import (
    HealthStatus,
    PluginHealth,
    PluginInstallContext,
    PluginMetadata,
)
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec, subscribes_to
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED, FeatureSet
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED, IndicatorsUpdatedPayload
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
    PUBLISHED_EVENTS,
    SUBSCRIBED_EVENTS,
    PatternDetectedPayload,
    PatternInvalidatedPayload,
    PatternUpdatedPayload,
)
from tranalyset.plugins.pattern_intelligence_engine.service import (
    PatternCandidate,
    PatternIntelligenceService,
    PatternIntelligenceSettings,
)

ServiceType = TypeVar("ServiceType")


class PatternIntelligenceEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="pattern_intelligence_engine",
                name="Pattern Intelligence Engine",
                version="1.0.0",
                depends_on=frozenset(
                    {
                        "market_data",
                        "feature_engine",
                        "indicator_engine",
                        "market_state_engine",
                    }
                ),
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("history_limit", int, required=False, default=120),
                    ConfigurationField("max_tracked_series", int, required=False, default=5_000),
                    ConfigurationField("lookback_bars", int, required=False, default=30),
                    ConfigurationField("min_confidence", float, required=False, default=0.55),
                    ConfigurationField("min_strength", float, required=False, default=55.0),
                    ConfigurationField("update_delta", float, required=False, default=0.08),
                    ConfigurationField("confirmation_age", int, required=False, default=2),
                    ConfigurationField(
                        "trend_confirm_threshold", float, required=False, default=0.55
                    ),
                    ConfigurationField(
                        "volume_confirm_threshold", float, required=False, default=0.55
                    ),
                    ConfigurationField(
                        "volatility_threshold", float, required=False, default=0.4
                    ),
                    ConfigurationField("regime_threshold", float, required=False, default=0.5),
                )
            ),
        )
        self._service: PatternIntelligenceService | None = None
        self._event_counts = {str(event_type): 0 for event_type in SUBSCRIBED_EVENTS}

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(PatternIntelligenceEnginePlugin, self)
        context.register_service_factory(
            PatternIntelligenceService,
            lambda _container: self._ensure_service(),
            replace=True,
        )
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Pattern intelligence engine publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            raise RuntimeError("Pattern intelligence engine has no runtime context.")
        settings = PatternIntelligenceSettings(
            history_limit=self.configuration.get("history_limit", int),
            max_tracked_series=self.configuration.get("max_tracked_series", int),
            lookback_bars=self.configuration.get("lookback_bars", int),
            min_confidence=self.configuration.get("min_confidence", float),
            min_strength=self.configuration.get("min_strength", float),
            update_delta=self.configuration.get("update_delta", float),
            confirmation_age=self.configuration.get("confirmation_age", int),
            trend_confirm_threshold=self.configuration.get("trend_confirm_threshold", float),
            volume_confirm_threshold=self.configuration.get("volume_confirm_threshold", float),
            volatility_threshold=self.configuration.get("volatility_threshold", float),
            regime_threshold=self.configuration.get("regime_threshold", float),
        )
        repository = self._resolve(MarketDataRepository)
        indicator_cache = self._resolve(IndicatorCache)
        aggregation_engine = self._resolve(TimeframeAggregationEngine)
        self._service = PatternIntelligenceService(
            repository=repository,
            indicator_cache=indicator_cache,
            aggregation_engine=aggregation_engine,
            settings=settings,
        )

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._evaluate_candle(event)

    @subscribes_to(TIMEFRAME_CANDLE_CLOSED)
    async def handle_timeframe_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        await self._evaluate_candle(event)

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        payload = event.payload
        await self._ensure_service().update_indicator_context(
            IndicatorsUpdatedPayload(
                symbol=_text(payload.get("symbol")),
                exchange=_text(
                    payload.get("exchange"),
                    default=_text(payload.get("venue"), default="UNKNOWN"),
                ),
                timeframe=_timeframe(payload).value,
                observed_at=_timestamp(payload, event.timestamp),
                indicators=_mapping(payload.get("indicators")),
                versions=_mapping(payload.get("versions")),
                source_event_type=str(event.event_type),
            )
        )

    @subscribes_to(FEATURES_UPDATED)
    async def handle_features_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        payload = event.payload
        observed_at = _timestamp(payload, event.timestamp)
        features = payload.get("features")
        feature_values = {
            key: float(value)
            for key, value in _mapping(features).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if not feature_values:
            return
        await self._ensure_service().update_feature_context(
            FeatureSet(
                feature_set_id=_text(
                    payload.get("feature_set_id"),
                    default=f"feature-{event.timestamp.timestamp():.0f}",
                ),
                symbol=_text(payload.get("symbol")),
                exchange=_text(
                    payload.get("exchange"),
                    default=_text(payload.get("venue"), default="UNKNOWN"),
                ),
                timeframe=_timeframe(payload).value,
                observed_at=observed_at,
                generated_at=_timestamp(payload, event.timestamp, keys=("generated_at",)),
                source_event_type=str(event.event_type),
                features=feature_values,
                metadata=_mapping(payload.get("metadata")),
            )
        )

    @subscribes_to(MARKET_REGIME_CHANGED)
    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        payload = event.payload
        regime = _text(payload.get("regime"))
        if not regime:
            return
        await self._ensure_service().update_market_regime(
            symbol=_text(payload.get("symbol")),
            exchange=_text(
                payload.get("exchange"),
                default=_text(payload.get("venue"), default="UNKNOWN"),
            ),
            timeframe=_timeframe(payload),
            regime=regime,
            confidence=_number(payload.get("confidence")),
            observed_at=_timestamp(payload, event.timestamp),
            source_event_type=str(event.event_type),
        )

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._service is None or self._service.generated == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Pattern intelligence engine is running before pattern observations.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Pattern intelligence engine is evaluating candle and market context.",
            details={**self._event_counts, **(await self._service.health_snapshot())},
        )

    async def _evaluate_candle(self, event: EventEnvelope) -> None:
        payload = event.payload
        open_price = _number(payload.get("open_price", payload.get("open")))
        candle = Candle(
            symbol=_text(payload.get("symbol")),
            exchange=_text(payload.get("exchange"), default=_text(payload.get("venue"), default="UNKNOWN")),
            timeframe=_timeframe(payload),
            timestamp=_timestamp(
                payload,
                event.timestamp,
                keys=("opened_at", "observed_at", "closed_at", "timestamp", "received_at"),
            ),
            open=open_price,
            high=_number(payload.get("high_price", payload.get("high")), default=open_price),
            low=_number(payload.get("low_price", payload.get("low")), default=open_price),
            close=_number(payload.get("close_price", payload.get("close")), default=open_price),
            volume=_number(payload.get("volume", payload.get("size"))),
            trades=int(_number(payload.get("trades"))),
        )
        candidates = await self._ensure_service().ingest_candle(
            candle,
            source_event_type=str(event.event_type),
        )
        for candidate in candidates:
            await self._publish_candidate(candidate, candle)

    async def _publish_candidate(self, candidate: PatternCandidate, candle: Candle) -> None:
        common: dict[str, Any] = {
            "symbol": candle.symbol,
            "exchange": candle.exchange,
            "timeframe": candle.timeframe.value,
            "observed_at": candle.timestamp,
            "pattern_id": candidate.pattern_id,
            "pattern": candidate.pattern,
            "category": candidate.category,
            "confidence": candidate.confidence,
            "strength": candidate.strength,
            "age_bars": candidate.age_bars,
            "historical_success_rate": candidate.historical_success_rate,
            "reliability": candidate.reliability,
            "current_context": candidate.current_context,
            "volume_confirmation": candidate.volume_confirmation,
            "trend_confirmation": candidate.trend_confirmation,
            "volatility": candidate.volatility,
            "market_regime": candidate.market_regime,
            "evidence": candidate.evidence,
            "source_event_type": candidate.source_event_type,
        }
        if not candidate.validity:
            payload = PatternInvalidatedPayload(
                **common,
                reason=candidate.reason or "Pattern invalidated by new observations.",
            )
            await self.publish(
                PATTERN_INVALIDATED,
                payload=payload.to_payload(),
            )
        elif candidate.previous_confidence is not None or candidate.previous_strength is not None:
            payload = PatternUpdatedPayload(
                **common,
                validity=True,
                previous_confidence=candidate.previous_confidence,
                previous_strength=candidate.previous_strength,
            )
            await self.publish(PATTERN_UPDATED, payload=payload.to_payload())
        else:
            payload = PatternDetectedPayload(**common, validity=True)
            await self.publish(PATTERN_DETECTED, payload=payload.to_payload())

    def _resolve(self, service_type: type[ServiceType]) -> ServiceType:
        if self._context is None:
            raise RuntimeError("Pattern intelligence engine has no runtime context.")
        service = self._context.resolve(service_type)
        if not isinstance(service, service_type):
            raise TypeError(
                f"Pattern intelligence requires {service_type.__name__} from the service container."
            )
        return service

    def _ensure_service(self) -> PatternIntelligenceService:
        if self._service is None:
            raise RuntimeError("Pattern intelligence service is not configured.")
        return self._service

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object, *, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _timestamp(
    payload: Any,
    fallback: datetime,
    *,
    keys: tuple[str, ...] = ("observed_at", "closed_at", "timestamp", "received_at"),
) -> datetime:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(UTC)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError(f"{key} must include a timezone.")
            return parsed.astimezone(UTC)
    if fallback.tzinfo is None:
        raise ValueError("Event timestamp must include a timezone.")
    return fallback.astimezone(UTC)


def _timeframe(payload: Any) -> Timeframe:
    value = payload.get("timeframe")
    return Timeframe(value) if isinstance(value, str) and value else Timeframe.ONE_MINUTE


def create_plugin() -> PatternIntelligenceEnginePlugin:
    return PatternIntelligenceEnginePlugin()


__all__ = ["PatternIntelligenceEnginePlugin", "create_plugin"]
