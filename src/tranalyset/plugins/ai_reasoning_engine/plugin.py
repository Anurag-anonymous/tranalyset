from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tranalyset.contracts.events import EventEnvelope, EventType, ImmutableValue
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.kernel.container import ServiceNotFoundError
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.ai_reasoning_engine.contracts import (
    PUBLISHED_EVENTS,
    REASONING_GENERATED,
    ReasoningGeneratedPayload,
)
from tranalyset.plugins.knowledge_engine.contracts import KnowledgeSourceType
from tranalyset.plugins.ai_reasoning_engine.reasoning import (
    DeterministicReasoningProvider,
    EvidenceReference,
    EvidenceRetriever,
    ReasoningProvider,
    ReasoningRequest,
    evidence_from_event,
)
from tranalyset.plugins.candlestick.contracts import PATTERN_DETECTED, PATTERN_INVALIDATED
from tranalyset.plugins.market_state_engine.contracts import (
    LIQUIDITY_CHANGED,
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
)
from tranalyset.plugins.risk.contracts import (
    POSITION_SIZE_CALCULATED,
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
)
from tranalyset.plugins.trade_health_engine.contracts import (
    CONFIDENCE_CHANGED,
    TRADE_HEALTH_UPDATED,
    TRADE_STRENGTH_CHANGED,
)
from tranalyset.plugins.trade_manager.contracts import (
    TRADE_SIGNAL_GENERATED,
    TRAILING_STOP_UPDATED,
)

MAJOR_REASONING_EVENTS = (
    TRADE_HEALTH_UPDATED,
    CONFIDENCE_CHANGED,
    TRADE_STRENGTH_CHANGED,
    RISK_APPROVED,
    RISK_REJECTED,
    POSITION_SIZE_CALCULATED,
    STOP_LOSS_UPDATED,
    TRADE_SIGNAL_GENERATED,
    TRAILING_STOP_UPDATED,
    MARKET_REGIME_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
    LIQUIDITY_CHANGED,
    MOMENTUM_STATE_CHANGED,
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
)


class AIReasoningEnginePlugin(BasePlugin):
    def __init__(
        self,
        *,
        reasoning_provider: ReasoningProvider | None = None,
        evidence_retriever: EvidenceRetriever | None = None,
    ) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="ai_reasoning_engine",
                name="AI Reasoning Engine",
                version="1.0.0",
            )
        )
        self._provider = reasoning_provider or DeterministicReasoningProvider()
        self._retriever = evidence_retriever
        self._event_counts = {str(event_type): 0 for event_type in MAJOR_REASONING_EVENTS}
        self._generated_count = 0
        self._last_reasoning_id: str | None = None

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(AIReasoningEnginePlugin, self)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"AI reasoning engine plugin publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            return
        if isinstance(self._provider, DeterministicReasoningProvider):
            try:
                resolved_provider = self._context.resolve(ReasoningProvider)
            except ServiceNotFoundError:
                resolved_provider = None
            if resolved_provider is not ReasoningProvider and isinstance(resolved_provider, ReasoningProvider):
                self._provider = resolved_provider
        if self._retriever is None:
            try:
                resolved_retriever = self._context.resolve(EvidenceRetriever)
            except ServiceNotFoundError:
                resolved_retriever = None
            if resolved_retriever is not EvidenceRetriever and isinstance(resolved_retriever, EvidenceRetriever):
                self._retriever = resolved_retriever

    @subscribes_to(TRADE_HEALTH_UPDATED)
    async def handle_trade_health_updated(self, event: EventEnvelope) -> None:
        await self._explain(event, "trade_health")

    @subscribes_to(CONFIDENCE_CHANGED)
    async def handle_confidence_changed(self, event: EventEnvelope) -> None:
        await self._explain(event, "trade_health")

    @subscribes_to(TRADE_STRENGTH_CHANGED)
    async def handle_trade_strength_changed(self, event: EventEnvelope) -> None:
        await self._explain(event, "trade_health")

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        await self._explain(event, "risk")

    @subscribes_to(RISK_REJECTED)
    async def handle_risk_rejected(self, event: EventEnvelope) -> None:
        await self._explain(event, "risk")

    @subscribes_to(POSITION_SIZE_CALCULATED)
    async def handle_position_size_calculated(self, event: EventEnvelope) -> None:
        await self._explain(event, "risk")

    @subscribes_to(STOP_LOSS_UPDATED)
    async def handle_stop_loss_updated(self, event: EventEnvelope) -> None:
        await self._explain(event, "risk")

    @subscribes_to(TRADE_SIGNAL_GENERATED)
    async def handle_trade_signal_generated(self, event: EventEnvelope) -> None:
        category = _candidate_category(event.payload)
        if category is None:
            return
        await self._explain(event, category)

    @subscribes_to(TRAILING_STOP_UPDATED)
    async def handle_trailing_stop_updated(self, event: EventEnvelope) -> None:
        await self._explain(event, "trailing_stop")

    @subscribes_to(MARKET_REGIME_CHANGED)
    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        await self._explain(event, "market_regime")

    @subscribes_to(TREND_CONFIRMED)
    async def handle_trend_confirmed(self, event: EventEnvelope) -> None:
        await self._explain(event, "market_regime")

    @subscribes_to(TREND_WEAKENED)
    async def handle_trend_weakened(self, event: EventEnvelope) -> None:
        await self._explain(event, "market_regime")

    @subscribes_to(VOLATILITY_CHANGED)
    async def handle_volatility_changed(self, event: EventEnvelope) -> None:
        await self._explain(event, "market_regime")

    @subscribes_to(LIQUIDITY_CHANGED)
    async def handle_liquidity_changed(self, event: EventEnvelope) -> None:
        await self._explain(event, "market_regime")

    @subscribes_to(MOMENTUM_STATE_CHANGED)
    async def handle_momentum_state_changed(self, event: EventEnvelope) -> None:
        await self._explain(event, "market_regime")

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        await self._explain(event, "pattern_detection")

    @subscribes_to(PATTERN_INVALIDATED)
    async def handle_pattern_invalidated(self, event: EventEnvelope) -> None:
        await self._explain(event, "pattern_detection")

    async def publish_reasoning_generated(self, payload: ReasoningGeneratedPayload) -> None:
        await self.publish(REASONING_GENERATED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._generated_count == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="AI reasoning engine is running before explainable decisions.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="AI reasoning engine is generating evidence-bound explanations.",
            details={
                **self._event_counts,
                "generated_count": self._generated_count,
                "last_reasoning_id": self._last_reasoning_id,
                "provider": type(self._provider).__name__,
                "rag_enabled": self._retriever is not None,
            },
        )

    async def _explain(self, event: EventEnvelope, category: str) -> None:
        self._count(event.event_type)
        evidence = evidence_from_event(event)
        retrieved_context = await self._retrieve(event)
        if retrieved_context:
            evidence["retrieved_context"] = tuple(
                {
                    **item.to_payload(),
                    "source_type": _normalize_source_type(item.source),
                }
                for item in retrieved_context
            )
        subject = _subject(event)
        request = ReasoningRequest(
            category=category,
            source_event=event,
            subject=subject,
            evidence=evidence,
            retrieved_context=retrieved_context,
        )
        response = await self._provider.generate(request)
        reasoning_id = str(uuid4())
        await self.publish_reasoning_generated(
            ReasoningGeneratedPayload(
                reasoning_id=reasoning_id,
                generated_at=datetime.now(UTC),
                category=category,
                source_event_type=str(event.event_type),
                source_event_timestamp=event.timestamp,
                subject=subject,
                summary=response.summary,
                explanation=response.explanation,
                evidence=evidence,
                limitations=response.limitations,
                provider=response.provider,
                model=response.model,
            )
        )
        self._generated_count += 1
        self._last_reasoning_id = reasoning_id

    async def _retrieve(self, event: EventEnvelope) -> tuple[EvidenceReference, ...]:
        if self._retriever is None:
            return ()
        return tuple(await self._retriever.retrieve(event))

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def _candidate_category(payload: Mapping[str, ImmutableValue]) -> str | None:
    candidate_type = _text(payload.get("candidate_type"), default="")
    if candidate_type in {"EntryCandidate", "TradeSignalCandidate", "WatchCandidate"}:
        return "entry_candidate"
    if candidate_type in {"ExitCandidate", "PartialExitCandidate"}:
        return "exit_candidate"
    if candidate_type == "TrailCandidate":
        return "trailing_stop"
    return None


def _subject(event: EventEnvelope) -> dict[str, str]:
    payload = event.payload
    subject = {
        "event_type": str(event.event_type),
        "source": event.source,
    }
    for key in ("trade_id", "signal_id", "symbol", "timeframe", "candidate_type", "pattern", "regime", "state"):
        value = _text(payload.get(key), default="")
        if value:
            subject[key] = value
    if "symbol" not in subject:
        symbols = payload.get("symbols")
        if isinstance(symbols, tuple) and symbols:
            subject["symbols"] = ",".join(str(symbol) for symbol in symbols if isinstance(symbol, str))
    return subject


def _text(value: ImmutableValue | object, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def _normalize_source_type(source: str) -> str:
    return source.replace(" ", "_").lower()


def create_plugin() -> AIReasoningEnginePlugin:
    return AIReasoningEnginePlugin()
