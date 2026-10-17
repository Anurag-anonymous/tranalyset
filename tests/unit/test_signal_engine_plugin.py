from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import cast

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.candlestick import PATTERN_DETECTED
from tranalyset.plugins.market_state_engine import MARKET_REGIME_CHANGED
from tranalyset.plugins.news import NEWS_ANALYZED
from tranalyset.plugins.risk import RISK_APPROVED
from tranalyset.plugins.signal_engine import (
    CANDIDATE_TYPES,
    EntryCandidate,
    SignalCandidatePayload,
    SignalEnginePlugin,
    TradeSignalCandidate,
)
from tranalyset.plugins.technical_analysis import INDICATORS_UPDATED
from tranalyset.plugins.trade_health_engine import TRADE_HEALTH_UPDATED
from tranalyset.plugins.trade_manager import TRADE_SIGNAL_GENERATED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "signal_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "signal_engine"
        self.events: list[EventType] = []

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
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
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_plugin() -> tuple[SignalEnginePlugin, CapturingContext]:
    plugin = SignalEnginePlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_signal_engine_metadata_is_versioned() -> None:
    plugin = SignalEnginePlugin()

    assert plugin.metadata.plugin_id == "signal_engine"
    assert plugin.metadata.name == "Signal Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_signal_engine_installs_trade_signal_generated_event_contract() -> None:
    plugin = SignalEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert install_context.events == [TRADE_SIGNAL_GENERATED]


def test_signal_engine_subscribes_to_required_events() -> None:
    plugin = SignalEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {
        TRADE_HEALTH_UPDATED,
        MARKET_REGIME_CHANGED,
        PATTERN_DETECTED,
        INDICATORS_UPDATED,
        NEWS_ANALYZED,
        RISK_APPROVED,
    }


def test_signal_candidate_payload_requires_candidate_fields() -> None:
    payload = TradeSignalCandidate(
        signal_id="signal-1",
        symbol="AAPL",
        timeframe="1m",
        generated_at=_now(),
        confidence=0.78,
        reasoning=("Evidence supports a candidate.",),
        supporting_evidence={"health_score": 76.0},
        reward_risk_estimate=1.8,
        source_event_type=str(TRADE_HEALTH_UPDATED),
    ).to_payload()

    assert payload["candidate_type"] == "TradeSignalCandidate"
    assert payload["confidence"] == 0.78
    assert payload["reasoning"]
    assert payload["supporting_evidence"]["health_score"] == 76.0
    assert payload["reward_risk_estimate"] == 1.8


def test_signal_candidate_payload_rejects_invalid_candidate_type() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        SignalCandidatePayload(
            candidate_type="Invalid",
            signal_id="signal-1",
            symbol="AAPL",
            timeframe="1m",
            generated_at=_now(),
            confidence=0.78,
            reasoning=("Evidence supports a candidate.",),
            supporting_evidence={"health_score": 76.0},
            reward_risk_estimate=1.8,
            source_event_type=str(TRADE_HEALTH_UPDATED),
        ).to_payload()


async def test_signal_engine_generates_candidates_from_aggregated_evidence() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_indicators_updated(
        EventEnvelope.create(
            source="technical_analysis",
            event_type=INDICATORS_UPDATED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "indicators": {
                    "trend_score": 0.8,
                    "momentum_score": 0.75,
                    "volume_ratio": 1.6,
                    "reward_risk_estimate": 2.1,
                },
            },
        )
    )
    await plugin.handle_pattern_detected(
        EventEnvelope.create(
            source="candlestick",
            event_type=PATTERN_DETECTED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "pattern": "engulfing",
                "confidence": 0.72,
            },
        )
    )
    await plugin.handle_market_regime_changed(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=MARKET_REGIME_CHANGED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "regime": "Bull Trend",
                "confidence": 0.8,
            },
        )
    )
    await plugin.handle_news_analyzed(
        EventEnvelope.create(
            source="news",
            event_type=NEWS_ANALYZED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "sentiment": "positive",
                "confidence": 0.66,
            },
        )
    )
    await plugin.handle_trade_health_updated(
        EventEnvelope.create(
            source="trade_health_engine",
            event_type=TRADE_HEALTH_UPDATED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "health_score": 82.0,
                "confidence_lower": 74.0,
                "confidence_upper": 88.0,
                "factors": {"trend": 88.0, "momentum": 84.0, "volume": 75.0},
                "explanations": ("Health improved.",),
            },
        )
    )
    await plugin.handle_risk_approved(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "reward_risk_estimate": 2.4,
            },
        )
    )

    candidates = [event.payload for event in context.published]
    candidate_types = {cast(str, candidate["candidate_type"]) for candidate in candidates}

    assert TRADE_SIGNAL_GENERATED in {event.event_type for event in context.published}
    assert "TradeSignalCandidate" in candidate_types
    assert "EntryCandidate" in candidate_types
    assert "TrailCandidate" in candidate_types
    assert candidates[-1]["reward_risk_estimate"] == 2.4
    assert candidates[-1]["reasoning"]
    assert candidates[-1]["supporting_evidence"]


async def test_signal_engine_generates_watch_and_exit_candidates() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_trade_health_updated(
        EventEnvelope.create(
            source="trade_health_engine",
            event_type=TRADE_HEALTH_UPDATED,
            payload={
                "trade_id": "trade-watch",
                "symbol": "MSFT",
                "timeframe": "1m",
                "health_score": 60.0,
                "confidence_lower": 45.0,
                "confidence_upper": 70.0,
                "factors": {"trend": 55.0},
                "explanations": ("Neutral evidence.",),
            },
        )
    )
    await plugin.handle_trade_health_updated(
        EventEnvelope.create(
            source="trade_health_engine",
            event_type=TRADE_HEALTH_UPDATED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "health_score": 52.0,
                "confidence_lower": 40.0,
                "confidence_upper": 60.0,
                "factors": {"trend": 45.0},
                "explanations": ("Mixed evidence.",),
            },
        )
    )
    await plugin.handle_market_regime_changed(
        EventEnvelope.create(
            source="market_state_engine",
            event_type=MARKET_REGIME_CHANGED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "regime": "Bear Trend",
                "confidence": 0.8,
            },
        )
    )

    candidate_types = [event.payload["candidate_type"] for event in context.published]

    assert "WatchCandidate" in candidate_types
    assert "PartialExitCandidate" in candidate_types
    assert "ExitCandidate" in candidate_types


async def test_signal_engine_integrates_with_event_bus() -> None:
    plugin = SignalEnginePlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    await kernel.event_bus.subscribe(TRADE_SIGNAL_GENERATED, collect)
    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="trade_health_engine",
            event_type=TRADE_HEALTH_UPDATED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "health_score": 78.0,
                "confidence_lower": 70.0,
                "confidence_upper": 85.0,
                "factors": {"trend": 80.0, "momentum": 82.0},
                "explanations": ("Health improved.",),
            },
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "reward_risk_estimate": 2.0,
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("signal_engine")
    await kernel.stop()

    assert published
    assert {cast(str, event.payload["candidate_type"]) for event in published} >= {
        "TradeSignalCandidate",
        "EntryCandidate",
    }
    assert health.status is HealthStatus.HEALTHY


async def test_signal_engine_evicts_stale_evidence() -> None:
    import asyncio

    # Use a very short TTL so old entries become stale during the test
    plugin = SignalEnginePlugin(evidence_ttl_seconds=0.01)
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)

    # Add evidence for two trade keys
    await plugin.handle_trade_health_updated(
        EventEnvelope.create(
            source="trade_health_engine",
            event_type=TRADE_HEALTH_UPDATED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "health_score": 82.0,
                "confidence_lower": 74.0,
                "confidence_upper": 88.0,
                "factors": {"trend": 80.0, "momentum": 82.0},
                "explanations": ("Health improved.",),
            },
        )
    )
    await plugin.handle_trade_health_updated(
        EventEnvelope.create(
            source="trade_health_engine",
            event_type=TRADE_HEALTH_UPDATED,
            payload={
                "trade_id": "trade-2",
                "symbol": "MSFT",
                "timeframe": "5m",
                "health_score": 45.0,
                "confidence_lower": 35.0,
                "confidence_upper": 55.0,
                "factors": {"trend": 40.0},
                "explanations": ("Weak evidence.",),
            },
        )
    )

    assert len(plugin._evidence_by_key) == 2

    # Wait for TTL to expire
    await asyncio.sleep(0.02)

    # Trigger eviction by publishing enough events to hit the check interval
    for _ in range(60):
        await plugin.handle_trade_health_updated(
            EventEnvelope.create(
                source="trade_health_engine",
                event_type=TRADE_HEALTH_UPDATED,
                payload={
                    "trade_id": "trade-keep",
                    "symbol": "KEEP",
                    "timeframe": "1m",
                    "health_score": 70.0,
                    "confidence_lower": 60.0,
                    "confidence_upper": 80.0,
                    "factors": {"trend": 70.0},
                    "explanations": ("Keeping.",),
                },
            )
        )

    # Stale entries should be evicted; only the most recent entry remains
    assert len(plugin._evidence_by_key) == 1
    assert list(plugin._evidence_by_key.keys())[0] == ("trade-keep", "KEEP", "1m")
    assert plugin._evictions > 0


def test_signal_engine_payloads_do_not_open_modify_or_calculate_risk() -> None:
    payload = TradeSignalCandidate(
        signal_id="signal-1",
        symbol="AAPL",
        timeframe="1m",
        generated_at=_now(),
        confidence=0.78,
        reasoning=("Evidence supports a candidate.",),
        supporting_evidence={"health_score": 76.0},
        reward_risk_estimate=1.8,
        source_event_type=str(TRADE_HEALTH_UPDATED),
    ).to_payload()

    forbidden = {
        "broker_order_id",
        "open_trade",
        "modify_trade",
        "position_size",
        "risk_approved_by_signal_engine",
        "stop_loss",
    }

    assert payload["candidate_type"] in CANDIDATE_TYPES
    assert forbidden.isdisjoint(payload)
