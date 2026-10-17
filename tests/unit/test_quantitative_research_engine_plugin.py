from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.replay import ReplayEngine
from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
from tranalyset.plugins.portfolio_engine.contracts import PORTFOLIO_UPDATED
from tranalyset.plugins.probability_engine.contracts import PROBABILITY_UPDATED
from tranalyset.plugins.quantitative_research_engine import (
    OPTIMIZATION_COMPLETED,
    PUBLISHED_EVENTS,
    QuantitativeResearchEnginePlugin,
    RESEARCH_COMPLETED,
    STRATEGY_EVALUATED,
    ResearchPeriod,
    ResearchSplit,
    StrategyResearchInput,
)
from tranalyset.plugins.paper_trading_engine.contracts import PAPER_TRADE_CLOSED
from tranalyset.plugins.quantitative_research_engine.contracts import (
    CANDLE_CLOSED,
    CHARACTER_CHANGED,
    INDICATORS_UPDATED,
    LIQUIDITY_DETECTED,
    MARKET_CONTEXT_UPDATED,
    MARKET_STRUCTURE_UPDATED,
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
    PROBABILITY_UPDATED as PROBABILITY_UPDATED_TYPE,
    STRUCTURE_BREAK,
    SUBSCRIBED_EVENTS,
    TICK_RECEIVED,
    TIMEFRAME_CANDLE_CLOSED,
    TRADE_HEALTH_UPDATED,
)
from tranalyset.plugins.quantitative_research_engine.contracts import ResearchCompletedPayload, StrategyEvaluatedPayload, OptimizationCompletedPayload
from tranalyset.plugins.quantitative_research_engine.plugin import create_plugin


class CapturingContext:
    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        self.plugin_id = "quantitative_research_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []
        self._backend = InMemoryStorageBackend()
        self._repository = MarketDataRepository(self._backend)
        self._event_bus = AsyncEventBus()
        self._aggregation_engine = TimeframeAggregationEngine(self._repository, self._event_bus)
        self._indicator_cache = IndicatorCache(self._repository, self._event_bus)
        self._replay_engine = ReplayEngine(self._repository, self._event_bus)

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        if key is MarketDataRepository:
            return self._repository
        if key is TimeframeAggregationEngine:
            return self._aggregation_engine
        if key is IndicatorCache:
            return self._indicator_cache
        if key is ReplayEngine:
            return self._replay_engine
        raise KeyError(key)


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "quantitative_research_engine"
        self.events: list[EventType] = []

    def register_event(self, event_type: EventType, *, description: str, replace: bool = False) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        return None

    def register_service_factory(self, key: object, factory: Any, *, singleton: bool = True, replace: bool = False) -> None:
        return None


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_plugin(*, config: dict[str, Any] | None = None) -> tuple[QuantitativeResearchEnginePlugin, CapturingContext]:
    plugin = QuantitativeResearchEnginePlugin()
    context = CapturingContext(config=config)
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def test_quantitative_research_engine_metadata_is_versioned() -> None:
    plugin = QuantitativeResearchEnginePlugin()

    assert plugin.metadata.plugin_id == "quantitative_research_engine"
    assert plugin.metadata.name == "Quantitative Research Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_quantitative_research_engine_installs_published_event_contracts() -> None:
    plugin = QuantitativeResearchEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_quantitative_research_engine_subscribes_to_research_evidence_events() -> None:
    plugin = QuantitativeResearchEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == set(SUBSCRIBED_EVENTS)
    assert all(len(handlers) == 1 for handlers in subscriptions.values())


async def test_quantitative_research_engine_publishes_required_event_methods() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_strategy_evaluated(
        StrategyEvaluatedPayload(
            run_id="run-1",
            strategy_id="strategy-a",
            evaluated_at=timestamp,
            parameters={"param_a": 1},
            metrics={"win_rate": 0.5, "sharpe_ratio": 1.2, "drawdown": 0.1},
            robustness_score=0.7,
            warnings=("sample warning",),
            source_event_type=str(STRATEGY_EVALUATED),
        )
    )
    await plugin.publish_optimization_completed(
        OptimizationCompletedPayload(
            run_id="opt-1",
            strategy_id="strategy-a",
            completed_at=timestamp,
            parameter_rankings=({"parameters": {"param_a": 1}, "robustness_score": 0.7},),
            best_parameters={"param_a": 1},
            robustness_score=0.7,
            overfitting_risk=0.2,
            warnings=("optimization warning",),
            source_event_type=str(OPTIMIZATION_COMPLETED),
        )
    )
    await plugin.publish_research_completed(
        ResearchCompletedPayload(
            run_id="research-1",
            strategy_id="strategy-a",
            completed_at=timestamp,
            summary="Research complete.",
            distributions={"sharpe_ratio": (1.2,)},
            warnings=("research warning",),
            source_event_type=str(RESEARCH_COMPLETED),
        )
    )

    assert [event.event_type for event in context.published] == [
        STRATEGY_EVALUATED,
        OPTIMIZATION_COMPLETED,
        RESEARCH_COMPLETED,
    ]
    assert all(event.source == "quantitative_research_engine" for event in context.published)
    assert context.published[0].payload["status"] == "not_evaluated"
    assert context.published[0].payload["metrics"] == {}
    assert context.published[0].payload["robustness_score"] is None
    assert context.published[1].payload["parameter_rankings"] == ()
    assert context.published[1].payload["best_parameters"] == {}
    assert context.published[1].payload["robustness_score"] is None
    assert context.published[1].payload["overfitting_risk"] is None


async def test_quantitative_research_engine_runs_strategy_evaluation_and_optimization() -> None:
    plugin, context = await _started_plugin()
    strategy_id = "strategy-a"
    parameter_grid = {"a": (1, 2), "b": (0.1, 0.2)}
    event_windows = (
        (
            EventEnvelope.create(source="market_data", event_type=CANDLE_CLOSED, payload={"symbol": "AAPL", "exchange": "NYSE", "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.5, "volume": 1000}),
        ),
    )

    result = await plugin.run_strategy_evaluation(
        strategy_id=strategy_id,
        parameters={"a": 1},
        event_windows=event_windows,
    )
    assert result.strategy_id == strategy_id
    assert result.status == "not_evaluated"
    assert result.metrics == {}
    assert result.robustness_score is None
    assert result.metadata["dataset_identity"]
    assert result.metadata["configuration"] == {"a": 1}
    other_parameters = await plugin.run_strategy_evaluation(
        strategy_id=strategy_id,
        parameters={"a": 1000},
        event_windows=event_windows,
    )
    assert other_parameters.status == result.status
    assert other_parameters.metrics == result.metrics
    assert other_parameters.robustness_score == result.robustness_score

    opt_result = await plugin.run_parameter_sweep(
        strategy_id=strategy_id,
        parameter_grid=parameter_grid,
        event_windows=event_windows,
    )
    assert opt_result.strategy_id == strategy_id
    assert opt_result.status == "not_evaluated"
    assert opt_result.parameter_rankings == ()
    assert opt_result.best_parameters == {}
    assert opt_result.robustness_score is None
    assert len(opt_result.metadata["configuration"]["parameter_grid"]["a"]) == 2
    assert context.published[-1].payload["status"] == "not_evaluated"


async def test_quantitative_research_laboratory_isolated_from_plugin_event_publication() -> None:
    plugin, context = await _started_plugin()
    start = _now()
    split = ResearchSplit(
        ResearchPeriod("training", start, start + timedelta(days=1)),
        ResearchPeriod("validation", start + timedelta(days=1), start + timedelta(days=2)),
        ResearchPeriod("out_of_sample", start + timedelta(days=2), start + timedelta(days=3)),
    )
    events = tuple(
        EventEnvelope.create(
            source="paper_trading_engine",
            event_type=PAPER_TRADE_CLOSED,
            timestamp=start + timedelta(days=day, hours=1),
            payload={
                "trade_id": f"trade-{day}", "symbol": "AAPL",
                "occurred_at": (start + timedelta(days=day, hours=1)).isoformat(),
                "strategy_id": "research", "quantity": 1.0, "average_price": 100.0, "exit_price": 101.0,
                "realized_pnl": 1.0, "commissions": 0.0, "slippage": 0.0, "side": "long",
            },
        )
        for day in (0, 1, 2)
    )

    report = await plugin.run_strategy_laboratory(
        inputs=(StrategyResearchInput("research", events, {"lookback": 10}),), split=split,
    )

    assert report.rankings[0].out_of_sample["net_profit"] == 1.0
    assert context.published == []
