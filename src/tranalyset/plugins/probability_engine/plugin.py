from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.kernel.container import ServiceNotFoundError
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec, subscribes_to
from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
)
from tranalyset.plugins.pattern_intelligence_engine.contracts import PATTERN_DETECTED, PATTERN_INVALIDATED, PATTERN_UPDATED
from tranalyset.plugins.probability_engine.contracts import (
    CONFIDENCE_UPDATED,
    PUBLISHED_EVENTS,
    PROBABILITY_UPDATED,
    SUBSCRIBED_EVENTS,
)
from tranalyset.plugins.probability_engine.service import (
    BayesianProbabilityModel,
    ProbabilityEngineService,
    ProbabilityEngineSettings,
    ProbabilitySnapshot,
    ProbabilityModel,
)
from tranalyset.plugins.risk.contracts import (
    POSITION_SIZE_CALCULATED,
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
)
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED


class ProbabilityEnginePlugin(BasePlugin):
    def __init__(self, *, model: Any | None = None) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="probability_engine",
                name="Probability Engine",
                version="1.0.0",
                depends_on=frozenset(
                    {
                        "context_engine",
                        "trade_health_engine",
                        "pattern_intelligence_engine",
                        "indicator_engine",
                        "market_structure_engine",
                        "risk",
                    }
                ),
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("history_limit", int, required=False, default=180),
                    ConfigurationField("max_tracked_keys", int, required=False, default=10_000),
                    ConfigurationField("prior_strength", float, required=False, default=2.0),
                    ConfigurationField("update_delta", float, required=False, default=0.03),
                    ConfigurationField("confidence_delta", float, required=False, default=0.02),
                    ConfigurationField("weights", dict, required=False, default={}),
                )
            ),
        )
        self._model = model
        self._service = ProbabilityEngineService()
        self._event_counts = {str(event_type): 0 for event_type in SUBSCRIBED_EVENTS}

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(ProbabilityEnginePlugin, self)
        context.register_service_factory(ProbabilityEngineService, lambda _container: self._service, replace=True)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Probability engine publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            msg = "Probability engine has no runtime context."
            raise RuntimeError(msg)
        settings = ProbabilityEngineSettings(
            history_limit=self.configuration.get("history_limit", int),
            max_tracked_keys=self.configuration.get("max_tracked_keys", int),
            prior_strength=self.configuration.get("prior_strength", float),
            update_delta=self.configuration.get("update_delta", float),
            confidence_delta=self.configuration.get("confidence_delta", float),
            weights=self.configuration.get("weights", dict),
        )
        model = self._model
        if model is None:
            try:
                resolved_model = self._context.resolve(ProbabilityModel)
            except (KeyError, ServiceNotFoundError):
                resolved_model = None
            if resolved_model is not None:
                model = resolved_model
            else:
                model = BayesianProbabilityModel()
        self._service = ProbabilityEngineService(settings=settings, model=model)

    @subscribes_to(MARKET_CONTEXT_UPDATED)
    async def handle_market_context_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_market_context(event.payload)
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(TRADE_HEALTH_UPDATED)
    async def handle_trade_health_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_trade_health(event.payload)
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_pattern(event.payload)
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(PATTERN_UPDATED)
    async def handle_pattern_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_pattern(event.payload)
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(PATTERN_INVALIDATED)
    async def handle_pattern_invalidated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_pattern(event.payload)
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_indicators(event.payload)
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(MARKET_STRUCTURE_UPDATED)
    async def handle_market_structure_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(STRUCTURE_BREAK)
    async def handle_structure_break(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(CHARACTER_CHANGED)
    async def handle_character_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(LIQUIDITY_DETECTED)
    async def handle_liquidity_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_structure(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_risk(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(RISK_REJECTED)
    async def handle_risk_rejected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_risk(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(POSITION_SIZE_CALCULATED)
    async def handle_position_size_calculated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_risk(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    @subscribes_to(STOP_LOSS_UPDATED)
    async def handle_stop_loss_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        service = self._ensure_service()
        previous = self._previous_snapshot(event.payload)
        snapshots = await service.observe_risk(event.payload, source_event_type=str(event.event_type))
        await self._publish_snapshots(snapshots, previous_confidence=None if previous is None else previous.confidence)

    async def publish_probability_updated(self, snapshot: ProbabilitySnapshot) -> None:
        await self.publish(PROBABILITY_UPDATED, payload=snapshot.to_probability_payload().to_payload())

    async def publish_confidence_updated(
        self,
        snapshot: ProbabilitySnapshot,
        *,
        previous_confidence: float | None,
    ) -> None:
        await self.publish(
            CONFIDENCE_UPDATED,
            payload=snapshot.to_confidence_payload(previous_confidence=previous_confidence).to_payload(),
        )

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        service = self._service
        if service is None or service.generated == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Probability engine is running before probability evidence.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Probability engine is aggregating evidence into trade-success probabilities.",
            details={**self._event_counts, "generated": service.generated},
        )

    async def _publish_snapshots(self, snapshots: tuple[ProbabilitySnapshot, ...], *, previous_confidence: float | None) -> None:
        for snapshot in snapshots:
            await self.publish_probability_updated(snapshot)
            if previous_confidence is None or abs(snapshot.confidence - previous_confidence) >= self._service._settings.confidence_delta:  # noqa: SLF001
                await self.publish_confidence_updated(snapshot, previous_confidence=previous_confidence)

    def _ensure_service(self) -> ProbabilityEngineService:
        if self._service is None:
            msg = "Probability engine service is not configured."
            raise RuntimeError(msg)
        return self._service

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1

    def _previous_snapshot(self, payload: Any) -> ProbabilitySnapshot | None:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        trade_id, symbol, exchange, timeframe = _key_from_payload(data)
        return self._service.latest_snapshot(
            trade_id=trade_id,
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
        )


def _key_from_payload(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    symbol = _text(payload, "symbol", default="GLOBAL")
    exchange = _text(payload, "exchange", default="GLOBAL")
    timeframe = _text(payload, "timeframe", default="GLOBAL")
    trade_id = _text(payload, "trade_id", default="")
    if not trade_id:
        trade_id = f"potential:{exchange}:{symbol}:{timeframe}"
    return trade_id, symbol, exchange, timeframe


def _text(payload: dict[str, Any], *keys: str, default: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return default


def create_plugin() -> ProbabilityEnginePlugin:
    return ProbabilityEnginePlugin()


__all__ = [
    "ProbabilityEnginePlugin",
    "create_plugin",
]
