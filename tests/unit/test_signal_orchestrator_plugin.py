from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.learning.contracts import STRATEGY_PERFORMANCE_UPDATED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.signal_orchestrator import (
    ACCEPTED_CANDIDATE,
    REJECTED_CANDIDATE,
    SignalOrchestratorPlugin,
    WATCH_CANDIDATE_GENERATED,
)
from tranalyset.plugins.trade_manager.contracts import TRADE_SIGNAL_GENERATED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "signal_orchestrator"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "signal_orchestrator"
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


async def _started_plugin(
    *, config: Mapping[str, object] | None = None
) -> tuple[SignalOrchestratorPlugin, CapturingContext]:
    plugin = SignalOrchestratorPlugin()
    context = CapturingContext(config=config)
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


async def _started_kernel(plugin: SignalOrchestratorPlugin) -> tuple[Microkernel, list[EventEnvelope]]:
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))
    await kernel.event_bus.subscribe(TRADE_SIGNAL_GENERATED, collect)
    await kernel.event_bus.subscribe(WATCH_CANDIDATE_GENERATED, collect)
    await kernel.event_bus.subscribe(ACCEPTED_CANDIDATE, collect)
    await kernel.event_bus.subscribe(REJECTED_CANDIDATE, collect)
    await kernel.start()
    return kernel, published


def test_signal_orchestrator_metadata_is_versioned() -> None:
    plugin = SignalOrchestratorPlugin()

    assert plugin.metadata.plugin_id == "signal_orchestrator"
    assert plugin.metadata.name == "Signal Orchestrator"
    assert plugin.metadata.version == "1.0.0"


async def test_orchestrator_installs_publish_events() -> None:
    plugin = SignalOrchestratorPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert WATCH_CANDIDATE_GENERATED in install_context.events
    assert ACCEPTED_CANDIDATE in install_context.events
    assert REJECTED_CANDIDATE in install_context.events


def _candidate_payload(
    *,
    signal_id: str,
    candidate_type: str,
    symbol: str,
    timeframe: str,
    confidence: float,
    reward_risk_estimate: float,
) -> dict[str, object]:
    return {
        "candidate_type": candidate_type,
        "signal_id": signal_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "generated_at": _now().isoformat(),
        "confidence": confidence,
        "reasoning": ("Candidate signal received.",),
        "supporting_evidence": {"confidence": confidence},
        "reward_risk_estimate": reward_risk_estimate,
        "source_event_type": str(TRADE_SIGNAL_GENERATED),
    }


async def test_orchestrator_deduplicates_and_ranks_candidates() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_trade_signal_generated(
        EventEnvelope.create(
            source="strategy-a",
            event_type=TRADE_SIGNAL_GENERATED,
            payload=_candidate_payload(
                signal_id="signal-a",
                candidate_type="EntryCandidate",
                symbol="AAPL",
                timeframe="1m",
                confidence=0.5,
                reward_risk_estimate=2.0,
            ),
        )
    )
    await plugin.handle_trade_signal_generated(
        EventEnvelope.create(
            source="strategy-b",
            event_type=TRADE_SIGNAL_GENERATED,
            payload=_candidate_payload(
                signal_id="signal-b",
                candidate_type="EntryCandidate",
                symbol="AAPL",
                timeframe="1m",
                confidence=0.6,
                reward_risk_estimate=2.2,
            ),
        )
    )

    accepted = [event for event in context.published if event.event_type == ACCEPTED_CANDIDATE]
    rejected = [event for event in context.published if event.event_type == REJECTED_CANDIDATE]

    assert len(accepted) == 2
    assert rejected == []
    assert accepted[-1].payload["signal_id"] == "signal-b"
    assert accepted[-1].payload["strategy_weight"] == 1.0


async def test_orchestrator_bounds_candidate_and_duplicate_id_retention() -> None:
    plugin, _ = await _started_plugin(
        config={"max_candidates": 2, "max_seen_signal_ids": 2}
    )
    for index, symbol in enumerate(("AAPL", "MSFT", "NVDA"), start=1):
        await plugin.handle_trade_signal_generated(
            EventEnvelope.create(
                source="strategy",
                event_type=TRADE_SIGNAL_GENERATED,
                payload=_candidate_payload(
                    signal_id=f"signal-{index}",
                    candidate_type="EntryCandidate",
                    symbol=symbol,
                    timeframe="1m",
                    confidence=0.6,
                    reward_risk_estimate=2.0,
                ),
            )
        )

    assert len(plugin._candidates) == 2
    assert len(plugin._groups) == 2
    assert len(plugin._seen_signal_ids) == 2
    assert ("AAPL", "1m", "EntryCandidate") not in plugin._candidates
    assert "signal-1" not in plugin._seen_signal_ids


async def test_orchestrator_applies_historical_performance_weighting() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_strategy_performance_updated(
        EventEnvelope.create(
            source="strategy-a",
            event_type=STRATEGY_PERFORMANCE_UPDATED,
            payload={
                "strategy_id": "strategy-a",
                "win_rate": 0.9,
                "average_reward_risk": 3.0,
                "average_holding_time_hours": 5.0,
            },
        )
    )
    await plugin.handle_trade_signal_generated(
        EventEnvelope.create(
            source="strategy-a",
            event_type=TRADE_SIGNAL_GENERATED,
            payload=_candidate_payload(
                signal_id="signal-a",
                candidate_type="EntryCandidate",
                symbol="AAPL",
                timeframe="1m",
                confidence=0.6,
                reward_risk_estimate=2.0,
            ),
        )
    )

    accepted = [event for event in context.published if event.event_type == ACCEPTED_CANDIDATE]
    assert accepted
    assert accepted[0].payload["strategy_weight"] > 1.0


async def test_orchestrator_generates_watch_candidate_event() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_trade_signal_generated(
        EventEnvelope.create(
            source="strategy-watch",
            event_type=TRADE_SIGNAL_GENERATED,
            payload=_candidate_payload(
                signal_id="signal-watch",
                candidate_type="WatchCandidate",
                symbol="MSFT",
                timeframe="5m",
                confidence=0.7,
                reward_risk_estimate=1.4,
            ),
        )
    )

    watch_events = [event for event in context.published if event.event_type == WATCH_CANDIDATE_GENERATED]
    assert len(watch_events) == 1
    assert watch_events[0].payload["decision"] == "watch"
    assert watch_events[0].payload["candidate_type"] == "WatchCandidate"


async def test_orchestrator_ignores_self_published_trade_signals() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_trade_signal_generated(
        EventEnvelope.create(
            source="signal_orchestrator",
            event_type=TRADE_SIGNAL_GENERATED,
            payload=_candidate_payload(
                signal_id="signal-self",
                candidate_type="EntryCandidate",
                symbol="AAPL",
                timeframe="1m",
                confidence=0.9,
                reward_risk_estimate=2.0,
            ),
        )
    )

    assert context.published == []


async def test_orchestrator_integrates_with_event_bus() -> None:
    plugin = SignalOrchestratorPlugin()
    kernel, published = await _started_kernel(plugin)

    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="strategy-a",
            event_type=TRADE_SIGNAL_GENERATED,
            payload=_candidate_payload(
                signal_id="signal-a",
                candidate_type="EntryCandidate",
                symbol="AAPL",
                timeframe="1m",
                confidence=0.8,
                reward_risk_estimate=2.0,
            ),
        )
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("signal_orchestrator")
    await kernel.stop()

    assert any(event.event_type == ACCEPTED_CANDIDATE for event in published)
    assert health.status is HealthStatus.HEALTHY
