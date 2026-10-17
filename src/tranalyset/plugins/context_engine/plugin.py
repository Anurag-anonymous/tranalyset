from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugin_sdk.configuration import ConfigurationField, PluginConfigurationSpec
from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED, PUBLISHED_EVENTS, SUBSCRIBED_EVENTS
from tranalyset.plugins.context_engine.service import ContextEngineService, ContextEngineSettings, MarketContextSnapshot
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_state_engine.contracts import (
    LIQUIDITY_CHANGED,
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
)
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
)
from tranalyset.plugins.news.contracts import MARKET_SENTIMENT_CHANGED, NEWS_ANALYZED
from tranalyset.plugins.pattern_intelligence_engine.contracts import PATTERN_DETECTED, PATTERN_INVALIDATED, PATTERN_UPDATED


class ContextEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="context_engine",
                name="Context Engine",
                version="1.0.0",
                depends_on=frozenset(
                    {
                        "feature_engine",
                        "indicator_engine",
                        "pattern_intelligence_engine",
                        "market_structure_engine",
                        "news",
                        "market_state_engine",
                    }
                ),
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("history_limit", int, required=False, default=180),
                    ConfigurationField("max_tracked_keys", int, required=False, default=5_000),
                    ConfigurationField("min_confidence", float, required=False, default=0.5),
                    ConfigurationField("update_delta", float, required=False, default=0.05),
                )
            ),
        )
        self._service = ContextEngineService()
        self._event_counts = {str(event_type): 0 for event_type in SUBSCRIBED_EVENTS}

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(ContextEnginePlugin, self)
        context.register_service_factory(ContextEngineService, lambda _container: self._service, replace=True)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Context engine publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            msg = "Context engine has no runtime context."
            raise RuntimeError(msg)
        settings = ContextEngineSettings(
            history_limit=self.configuration.get("history_limit", int),
            max_tracked_keys=self.configuration.get("max_tracked_keys", int),
            min_confidence=self.configuration.get("min_confidence", float),
            update_delta=self.configuration.get("update_delta", float),
        )
        self._service = ContextEngineService(settings=settings)

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_indicators(event.payload)
        await self._publish_snapshots(snapshots)

    @subscribes_to(FEATURES_UPDATED)
    async def handle_features_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_features(event.payload)
        await self._publish_snapshots(snapshots)

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_pattern(event.payload)
        await self._publish_snapshots(snapshots)

    @subscribes_to(PATTERN_UPDATED)
    async def handle_pattern_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_pattern(event.payload)
        await self._publish_snapshots(snapshots)

    @subscribes_to(PATTERN_INVALIDATED)
    async def handle_pattern_invalidated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_pattern(event.payload)
        await self._publish_snapshots(snapshots)

    @subscribes_to(MARKET_STRUCTURE_UPDATED)
    async def handle_market_structure_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots)

    @subscribes_to(STRUCTURE_BREAK)
    async def handle_structure_break(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots)

    @subscribes_to(CHARACTER_CHANGED)
    async def handle_character_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots)

    @subscribes_to(LIQUIDITY_DETECTED)
    async def handle_liquidity_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        snapshots = await self._ensure_service().observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots)

    @subscribes_to(NEWS_ANALYZED)
    async def handle_news_analyzed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        payload = event.payload
        symbols = _symbols(payload)
        snapshots = await self._ensure_service().observe_news(
            symbols=symbols,
            sentiment=_text(payload, "sentiment", default="neutral"),
            confidence=_number(payload, "confidence", default=0.5),
            evidence=_evidence(payload),
            observed_at=_timestamp(payload, default=event.timestamp),
            source_event_type=str(event.event_type),
        )
        await self._publish_snapshots(snapshots)

    @subscribes_to(MARKET_SENTIMENT_CHANGED)
    async def handle_market_sentiment_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        payload = event.payload
        snapshots = await self._ensure_service().observe_news(
            symbols=(),
            sentiment=_text(payload, "sentiment", default="neutral"),
            confidence=_number(payload, "confidence", default=0.5),
            evidence=_evidence(payload),
            observed_at=_timestamp(payload, default=event.timestamp),
            source_event_type=str(event.event_type),
            market=_text(payload, "market", default="GLOBAL"),
        )
        await self._publish_snapshots(snapshots)

    @subscribes_to(MARKET_REGIME_CHANGED)
    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        payload = event.payload
        snapshots = await self._ensure_service().observe_market_state(
            symbol=_text(payload, "symbol", default="GLOBAL"),
            exchange=_text(payload, "exchange", default="GLOBAL"),
            timeframe=_text(payload, "timeframe", default=payload.get("timeframe", "GLOBAL")),
            state=_text(payload, "regime", default="Range Bound"),
            confidence=_number(payload, "confidence", default=0.5),
            evidence=_evidence(payload),
            observed_at=_timestamp(payload, default=event.timestamp),
            source_event_type=str(event.event_type),
        )
        await self._publish_snapshots(snapshots)

    @subscribes_to(TREND_CONFIRMED)
    async def handle_trend_confirmed(self, event: EventEnvelope) -> None:
        await self._handle_state_change(event, default_state="Bull Trend")

    @subscribes_to(TREND_WEAKENED)
    async def handle_trend_weakened(self, event: EventEnvelope) -> None:
        await self._handle_state_change(event, default_state="Sideways")

    @subscribes_to(VOLATILITY_CHANGED)
    async def handle_volatility_changed(self, event: EventEnvelope) -> None:
        await self._handle_state_change(event, default_state="Normal Volatility")

    @subscribes_to(LIQUIDITY_CHANGED)
    async def handle_liquidity_changed(self, event: EventEnvelope) -> None:
        await self._handle_state_change(event, default_state="Normal Liquidity")

    @subscribes_to(MOMENTUM_STATE_CHANGED)
    async def handle_momentum_state_changed(self, event: EventEnvelope) -> None:
        await self._handle_state_change(event, default_state="Neutral Momentum")

    async def publish_market_context_updated(self, snapshot: MarketContextSnapshot) -> None:
        await self.publish(MARKET_CONTEXT_UPDATED, payload=snapshot.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        service = self._service
        if service is None or service.generated == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Context engine is running before contextual snapshots have been generated.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Context engine is synthesizing market context.",
            details={**self._event_counts, "generated": service.generated},
        )

    async def _handle_state_change(self, event: EventEnvelope, *, default_state: str) -> None:
        self._count(event.event_type)
        payload = event.payload
        snapshots = await self._ensure_service().observe_market_state(
            symbol=_text(payload, "symbol", default="GLOBAL"),
            exchange=_text(payload, "exchange", default="GLOBAL"),
            timeframe=_text(payload, "timeframe", default=payload.get("timeframe", "GLOBAL")),
            state=_text(payload, "state", default=default_state),
            confidence=_number(payload, "confidence", default=0.5),
            evidence=_evidence(payload),
            observed_at=_timestamp(payload, default=event.timestamp),
            source_event_type=str(event.event_type),
        )
        await self._publish_snapshots(snapshots)

    async def _publish_snapshots(self, snapshots: tuple[MarketContextSnapshot, ...]) -> None:
        for snapshot in snapshots:
            await self.publish_market_context_updated(snapshot)

    def _ensure_service(self) -> ContextEngineService:
        if self._service is None:
            msg = "Context engine service is not configured."
            raise RuntimeError(msg)
        return self._service

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def _text(payload: Mapping[str, Any], *keys: str, default: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return default


def _number(payload: Mapping[str, Any], key: str, *, default: float) -> float:
    value = payload.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _timestamp(payload: Mapping[str, Any], *, default: datetime) -> datetime:
    for key in ("observed_at", "analyzed_at", "occurred_at", "generated_at", "received_at", "timestamp"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return default.astimezone(UTC)


def _symbols(payload: Mapping[str, Any]) -> tuple[str, ...]:
    value = payload.get("symbols")
    if isinstance(value, tuple):
        return tuple(symbol for symbol in value if isinstance(symbol, str) and symbol)
    if isinstance(value, list):
        return tuple(symbol for symbol in value if isinstance(symbol, str) and symbol)
    symbol = payload.get("symbol")
    if isinstance(symbol, str) and symbol:
        return (symbol,)
    return ()


def _evidence(payload: Mapping[str, Any]) -> Mapping[str, float | str | bool]:
    evidence = payload.get("evidence")
    if isinstance(evidence, Mapping):
        return {str(key): value for key, value in evidence.items() if isinstance(value, (int, float, str, bool))}
    return {}


def create_plugin() -> ContextEnginePlugin:
    return ContextEnginePlugin()


__all__ = [
    "ContextEnginePlugin",
    "create_plugin",
]
