from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.learning import (
    LEARNING_INSIGHTS_GENERATED,
    LEARNING_UPDATED,
    STRATEGY_IMPROVED,
    STRATEGY_PERFORMANCE_UPDATED,
    LearningInsightsGeneratedPayload,
    LearningPlugin,
    LearningUpdatedPayload,
    StrategyImprovedPayload,
    StrategyPerformanceUpdatedPayload,
)
from tranalyset.plugins.learning.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.trade_manager import TRADE_CLOSED, TRADE_MODIFIED, TRADE_OPENED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "learning"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "learning"
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


async def _started_plugin() -> tuple[LearningPlugin, CapturingContext]:
    plugin = LearningPlugin()
    context = CapturingContext()
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_learning_plugin_metadata_is_versioned() -> None:
    plugin = LearningPlugin()

    assert plugin.metadata.plugin_id == "learning"
    assert plugin.metadata.name == "Learning"
    assert plugin.metadata.version == "1.0.0"


async def test_learning_plugin_installs_published_event_contracts() -> None:
    plugin = LearningPlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_learning_plugin_subscribes_to_trade_lifecycle_events() -> None:
    plugin = LearningPlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {TRADE_OPENED, TRADE_MODIFIED, TRADE_CLOSED}
    assert len(subscriptions[TRADE_OPENED]) == 1
    assert len(subscriptions[TRADE_MODIFIED]) == 1
    assert len(subscriptions[TRADE_CLOSED]) == 1


async def test_learning_plugin_publishes_required_events() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_learning_updated(
        LearningUpdatedPayload(
            model_name="post_trade_observation_model",
            updated_at=timestamp,
            observations_processed=12,
            metrics={"sample_count": 12.0, "loss": 0.21},
            source_event_type=str(TRADE_CLOSED),
        )
    )
    await plugin.publish_strategy_improved(
        StrategyImprovedPayload(
            strategy_id="mean_reversion_v1",
            improved_at=timestamp,
            improvement_summary="Adjusted observation weights from closed trade evidence.",
            evidence={"walk_forward_passed": True},
            confidence=0.64,
            source_event_type=str(TRADE_MODIFIED),
        )
    )

    assert [event.event_type for event in context.published] == [
        LEARNING_UPDATED,
        STRATEGY_IMPROVED,
    ]
    assert all(event.source == "learning" for event in context.published)
    assert context.published[0].payload["observations_processed"] == 12
    assert context.published[1].payload["strategy_id"] == "mean_reversion_v1"


async def test_learning_health_reflects_trade_lifecycle_evidence() -> None:
    plugin = LearningPlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    await kernel.start()
    initial_health = await kernel.plugin_health("learning")
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="trade_manager",
            event_type=TRADE_OPENED,
            payload={"trade_id": "trade-1", "symbol": "AAPL"},
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="trade_manager",
            event_type=TRADE_MODIFIED,
            payload={"trade_id": "trade-1", "symbol": "AAPL"},
        )
    )
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="trade_manager",
            event_type=TRADE_CLOSED,
            payload={"trade_id": "trade-1", "symbol": "AAPL"},
        )
    )
    await kernel.event_bus.wait_until_idle()
    ready_health = await kernel.plugin_health("learning")
    await kernel.stop()

    assert initial_health.status is HealthStatus.DEGRADED
    assert ready_health.status is HealthStatus.HEALTHY
    assert ready_health.details["trade_opened_seen"] == 1
    assert ready_health.details["trade_modified_seen"] == 1
    assert ready_health.details["trade_closed_seen"] == 1


def test_learning_updated_payload_validates_observation_count() -> None:
    payload = LearningUpdatedPayload(
        model_name="post_trade_observation_model",
        updated_at=_now(),
        observations_processed=-1,
    )

    with pytest.raises(ValueError, match="observations_processed"):
        payload.to_payload()


def test_strategy_improved_payload_validates_confidence() -> None:
    payload = StrategyImprovedPayload(
        strategy_id="mean_reversion_v1",
        improved_at=_now(),
        improvement_summary="Adjusted observation weights.",
        confidence=1.5,
    )

    with pytest.raises(ValueError, match="confidence"):
        payload.to_payload()


def test_learning_payloads_do_not_include_activation_or_order_instructions() -> None:
    payload = StrategyImprovedPayload(
        strategy_id="mean_reversion_v1",
        improved_at=_now(),
        improvement_summary="Adjusted observation weights.",
    ).to_payload()

    forbidden = {"activate", "action", "order", "recommendation", "side"}

    assert forbidden.isdisjoint(payload)


async def test_learning_plugin_generates_insights_and_performance_updates_from_closed_trades() -> None:
    plugin, context = await _started_plugin()

    await plugin.handle_trade_closed(
        EventEnvelope(
            timestamp=_now(),
            source="trade_manager",
            event_type=TRADE_CLOSED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "reason": "target hit",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "holding_time_hours": 6.0,
                "stop_loss_price": 95.0,
                "reward_risk": 2.0,
                "market_regime": "Bull Trend",
                "pattern": "pullback",
                "indicator": "EMA crossover",
            },
        )
    )

    assert [event.event_type for event in context.published] == [
        LEARNING_INSIGHTS_GENERATED,
        STRATEGY_PERFORMANCE_UPDATED,
    ]
    insights_payload = context.published[0].payload
    performance_payload = context.published[1].payload
    assert insights_payload["best_setups"][0]["symbol"] == "AAPL"
    assert insights_payload["optimal_holding_time_hours"] == 6.0
    assert insights_payload["optimal_rr"] == 2.0
    assert "stop loss" in insights_payload["common_mistakes"][0].lower()
    assert performance_payload["win_rate"] == 1.0
    assert performance_payload["average_reward_risk"] == 2.0
