from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.ai_reasoning_engine import (
    AIReasoningEnginePlugin,
    EvidenceReference,
    ReasoningGeneratedPayload,
    ReasoningProvider,
    ReasoningRequest,
    ReasoningResponse,
)
from tranalyset.plugins.ai_reasoning_engine.contracts import PUBLISHED_EVENTS, REASONING_GENERATED
from tranalyset.plugins.ai_reasoning_engine.plugin import MAJOR_REASONING_EVENTS
from tranalyset.plugins.candlestick import PATTERN_DETECTED, PATTERN_INVALIDATED
from tranalyset.plugins.market_state_engine import (
    LIQUIDITY_CHANGED,
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
)
from tranalyset.plugins.risk import (
    POSITION_SIZE_CALCULATED,
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
)
from tranalyset.plugins.trade_health_engine import (
    CONFIDENCE_CHANGED,
    TRADE_HEALTH_UPDATED,
    TRADE_STRENGTH_CHANGED,
)
from tranalyset.plugins.trade_manager import TRADE_SIGNAL_GENERATED, TRAILING_STOP_UPDATED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "ai_reasoning_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "ai_reasoning_engine"
        self.events: list[EventType] = []
        self.services: list[object] = []

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        self.services.append(key)

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[object], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        return None


class LLMReasoningProvider:
    def __init__(self) -> None:
        self.requests: list[ReasoningRequest] = []

    async def generate(self, request: ReasoningRequest) -> ReasoningResponse:
        self.requests.append(request)
        return ReasoningResponse(
            summary="LLM provider summarized only supplied evidence.",
            explanation=("LLM provider received the event payload and did not add market facts.",),
            limitations=("No external data was used by this test provider.",),
            provider="llm",
            model="test-model",
        )


class StaticEvidenceRetriever:
    async def retrieve(self, event: EventEnvelope) -> Sequence[EvidenceReference]:
        return (
            EvidenceReference(
                reference_id="ref-1",
                source=str(event.event_type),
                content={"source_event_type": str(event.event_type)},
            ),
        )


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_plugin(
    *,
    provider: ReasoningProvider | None = None,
    retriever: StaticEvidenceRetriever | None = None,
) -> tuple[AIReasoningEnginePlugin, CapturingContext]:
    plugin = AIReasoningEnginePlugin(reasoning_provider=provider, evidence_retriever=retriever)
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def _event(event_type: EventType, payload: Mapping[str, object], *, source: str) -> EventEnvelope:
    return EventEnvelope(
        timestamp=_now(),
        source=source,
        event_type=event_type,
        payload=payload,
        metadata={"correlation_id": "corr-1"},
    )


def test_ai_reasoning_engine_metadata_is_versioned() -> None:
    plugin = AIReasoningEnginePlugin()

    assert plugin.metadata.plugin_id == "ai_reasoning_engine"
    assert plugin.metadata.name == "AI Reasoning Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_ai_reasoning_engine_installs_event_contract_and_service() -> None:
    plugin = AIReasoningEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS
    assert install_context.services == [AIReasoningEnginePlugin]


def test_ai_reasoning_engine_subscribes_to_major_decision_events() -> None:
    plugin = AIReasoningEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(MAJOR_REASONING_EVENTS).issubset(subscriptions)
    assert set(subscriptions) == {
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
    }
    assert REASONING_GENERATED not in subscriptions


async def test_reasoning_generated_payload_validates_required_evidence() -> None:
    payload = ReasoningGeneratedPayload(
        reasoning_id="reasoning-1",
        generated_at=_now(),
        category="risk",
        source_event_type=str(RISK_APPROVED),
        source_event_timestamp=_now(),
        subject={"trade_id": "trade-1", "symbol": "AAPL"},
        summary="Risk event explained.",
        explanation=("Reason was present in the payload.",),
        evidence={"event": {"payload": {"reason": "approved"}}},
        limitations=("No external data was used.",),
    ).to_payload()

    assert payload["category"] == "risk"
    assert payload["source_event_type"] == str(RISK_APPROVED)
    assert payload["evidence"]["event"]["payload"]["reason"] == "approved"


def test_reasoning_payload_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="Unsupported reasoning category"):
        ReasoningGeneratedPayload(
            reasoning_id="reasoning-1",
            generated_at=_now(),
            category="forecast",
            source_event_type=str(RISK_APPROVED),
            source_event_timestamp=_now(),
            subject={"trade_id": "trade-1"},
            summary="Invalid.",
            explanation=("Invalid.",),
            evidence={"event": {"payload": {"reason": "approved"}}},
        ).to_payload()


async def test_trade_health_reasoning_uses_only_received_evidence() -> None:
    plugin, context = await _started_plugin()
    event = _event(
        TRADE_HEALTH_UPDATED,
        {
            "trade_id": "trade-1",
            "symbol": "AAPL",
            "timeframe": "1m",
            "health_score": 82.0,
            "confidence_lower": 74.0,
            "confidence_upper": 88.0,
            "factors": {"trend": 88.0, "momentum": 84.0},
            "explanations": ("Trend and momentum improved.",),
        },
        source="trade_health_engine",
    )

    await plugin.handle_trade_health_updated(event)

    published = context.published[-1]
    payload = published.payload
    explanation_text = " ".join(str(item) for item in payload["explanation"])

    assert published.event_type == REASONING_GENERATED
    assert payload["category"] == "trade_health"
    assert payload["subject"]["trade_id"] == "trade-1"
    assert payload["evidence"]["event"]["payload"]["health_score"] == 82.0
    assert "health_score=82.0" in explanation_text
    assert "No data outside the received event evidence was used." in payload["limitations"]


async def test_risk_entry_exit_trailing_market_and_pattern_events_are_explained() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_risk_rejected(
        _event(
            RISK_REJECTED,
            {
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "reason": "portfolio drawdown limit reached",
                "evidence": {"drawdown": 0.07},
            },
            source="risk",
        )
    )
    await plugin.handle_trade_signal_generated(
        _event(
            TRADE_SIGNAL_GENERATED,
            {
                "candidate_type": "EntryCandidate",
                "signal_id": "signal-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "confidence": 0.78,
                "reasoning": ("Health and risk evidence aligned.",),
                "supporting_evidence": {"health_score": 82.0},
                "reward_risk_estimate": 2.0,
            },
            source="signal_engine",
        )
    )
    await plugin.handle_trade_signal_generated(
        _event(
            TRADE_SIGNAL_GENERATED,
            {
                "candidate_type": "ExitCandidate",
                "signal_id": "signal-2",
                "symbol": "AAPL",
                "timeframe": "1m",
                "confidence": 0.7,
                "reasoning": ("Health weakened.",),
                "supporting_evidence": {"health_score": 45.0},
                "reward_risk_estimate": 1.2,
            },
            source="signal_engine",
        )
    )
    await plugin.handle_trailing_stop_updated(
        _event(
            TRAILING_STOP_UPDATED,
            {
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "trailing_stop_price": 104.0,
                "previous_trailing_stop_price": 101.0,
                "reason": "price advanced",
            },
            source="trade_manager",
        )
    )
    await plugin.handle_market_regime_changed(
        _event(
            MARKET_REGIME_CHANGED,
            {
                "symbol": "AAPL",
                "timeframe": "1m",
                "regime": "Bull Trend",
                "previous_regime": "Sideways",
                "confidence": 0.81,
                "evidence": {"trend_score": 0.8},
            },
            source="market_state_engine",
        )
    )
    await plugin.handle_pattern_detected(
        _event(
            PATTERN_DETECTED,
            {
                "symbol": "AAPL",
                "timeframe": "1m",
                "pattern": "engulfing",
                "confidence": 0.72,
                "evidence": {"body_ratio": 0.67},
            },
            source="candlestick",
        )
    )

    categories = [event.payload["category"] for event in context.published]

    assert categories == [
        "risk",
        "entry_candidate",
        "exit_candidate",
        "trailing_stop",
        "market_regime",
        "pattern_detection",
    ]


async def test_custom_llm_provider_and_rag_retriever_are_supported() -> None:
    provider = LLMReasoningProvider()
    plugin, context = await _started_plugin(
        provider=provider,
        retriever=StaticEvidenceRetriever(),
    )

    await plugin.handle_risk_approved(
        _event(
            RISK_APPROVED,
            {
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "reason": "risk within configured limit",
                "evidence": {"portfolio_risk": 0.02},
            },
            source="risk",
        )
    )

    payload = context.published[-1].payload

    assert provider.requests[-1].evidence["event"]["payload"]["reason"] == "risk within configured limit"
    assert provider.requests[-1].retrieved_context[0].reference_id == "ref-1"
    assert payload["provider"] == "llm"
    assert payload["model"] == "test-model"
    assert payload["evidence"]["retrieved_context"][0]["reference_id"] == "ref-1"


async def test_unknown_signal_candidate_is_not_explained() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_trade_signal_generated(
        _event(
            TRADE_SIGNAL_GENERATED,
            {
                "candidate_type": "UnsupportedCandidate",
                "signal_id": "signal-1",
                "symbol": "AAPL",
                "timeframe": "1m",
            },
            source="signal_engine",
        )
    )

    assert context.published == []


async def test_ai_reasoning_engine_integrates_with_event_bus() -> None:
    plugin = AIReasoningEnginePlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    await kernel.event_bus.subscribe(REASONING_GENERATED, collect)
    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "reason": "risk within configured limit",
                "evidence": {"portfolio_risk": 0.02},
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("ai_reasoning_engine")
    await kernel.stop()

    assert len(published) == 1
    assert published[0].payload["category"] == "risk"
    assert published[0].payload["source_event_type"] == str(RISK_APPROVED)
    assert health.status is HealthStatus.HEALTHY
    assert health.details["generated_count"] == 1


def test_reasoning_payloads_do_not_contain_action_fields_or_invented_prices() -> None:
    payload = ReasoningGeneratedPayload(
        reasoning_id="reasoning-1",
        generated_at=_now(),
        category="entry_candidate",
        source_event_type=str(TRADE_SIGNAL_GENERATED),
        source_event_timestamp=_now(),
        subject={"signal_id": "signal-1", "symbol": "AAPL"},
        summary="Entry candidate explained.",
        explanation=("Candidate confidence was present in the payload.",),
        evidence={"event": {"payload": {"confidence": 0.78}}},
        limitations=("No data outside the received event evidence was used.",),
    ).to_payload()

    forbidden = {
        "broker_order_id",
        "entry_price",
        "exit_price",
        "open_trade",
        "order",
        "position_size",
        "recommendation",
        "stop_loss",
    }

    assert forbidden.isdisjoint(payload)
    assert "price" not in str(payload["explanation"]).lower()
