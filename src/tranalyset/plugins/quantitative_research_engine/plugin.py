from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.market_data.replay import ReplayEngine
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec, subscribes_to
from tranalyset.plugins.quantitative_research_engine.contracts import (
    PUBLISHED_EVENTS,
    RESEARCH_COMPLETED,
    STRATEGY_EVALUATED,
    OPTIMIZATION_COMPLETED,
    SUBSCRIBED_EVENTS,
    ResearchCompletedPayload,
    StrategyEvaluatedPayload,
    OptimizationCompletedPayload,
)
from tranalyset.plugins.quantitative_research_engine.service import (
    OptimizationResult,
    QuantitativeResearchService,
    QuantitativeResearchSettings,
    ResearchReport,
    StrategyEvaluationResult,
)
from tranalyset.plugins.quantitative_research_engine.laboratory import (
    ResearchSplit,
    StrategyLaboratoryReport,
    StrategyResearchInput,
)
from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED, TIMEFRAME_CANDLE_CLOSED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
)
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
)
from tranalyset.plugins.portfolio_engine.contracts import PORTFOLIO_UPDATED
from tranalyset.plugins.probability_engine.contracts import PROBABILITY_UPDATED
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED


class QuantitativeResearchEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="quantitative_research_engine",
                name="Quantitative Research Engine",
                version="1.0.0",
                depends_on=frozenset(
                    {
                        "indicator_engine",
                        "market_structure_engine",
                        "probability_engine",
                    }
                ),
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("baseline_periods", int, required=False, default=5),
                    ConfigurationField("monte_carlo_trials", int, required=False, default=50),
                    ConfigurationField("bootstrap_trials", int, required=False, default=50),
                    ConfigurationField("sensitivity_delta", float, required=False, default=0.05),
                    ConfigurationField("stress_threshold", float, required=False, default=0.75),
                )
            ),
        )
        self._service: QuantitativeResearchService | None = None
        self._event_counts = {str(event_type): 0 for event_type in SUBSCRIBED_EVENTS}
        self._research_runs = 0
        self._experiment_ids: list[str] = []

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(QuantitativeResearchEnginePlugin, self)
        context.register_service_factory(QuantitativeResearchService, lambda _container: self._service, replace=True)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Quantitative research engine publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        if self._context is None:
            raise RuntimeError("Quantitative research engine has no runtime context.")
        repository = self._context.resolve(MarketDataRepository)
        replay_engine = self._context.resolve(ReplayEngine)
        indicator_cache = self._context.resolve(IndicatorCache)
        aggregation_engine = self._context.resolve(TimeframeAggregationEngine)

        settings = QuantitativeResearchSettings(
            baseline_periods=self.configuration.get("baseline_periods", int),
            monte_carlo_trials=self.configuration.get("monte_carlo_trials", int),
            bootstrap_trials=self.configuration.get("bootstrap_trials", int),
            sensitivity_delta=self.configuration.get("sensitivity_delta", float),
            stress_threshold=self.configuration.get("stress_threshold", float),
        )
        self._service = QuantitativeResearchService(
            repository=repository,
            replay_engine=replay_engine,
            indicator_cache=indicator_cache,
            aggregation_engine=aggregation_engine,
            settings=settings,
        )

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(TICK_RECEIVED)
    async def handle_tick_received(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(TIMEFRAME_CANDLE_CLOSED)
    async def handle_timeframe_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(PATTERN_UPDATED)
    async def handle_pattern_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(PATTERN_INVALIDATED)
    async def handle_pattern_invalidated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(MARKET_CONTEXT_UPDATED)
    async def handle_market_context_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(MARKET_STRUCTURE_UPDATED)
    async def handle_market_structure_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(STRUCTURE_BREAK)
    async def handle_structure_break(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(CHARACTER_CHANGED)
    async def handle_character_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(LIQUIDITY_DETECTED)
    async def handle_liquidity_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(PROBABILITY_UPDATED)
    async def handle_probability_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(TRADE_HEALTH_UPDATED)
    async def handle_trade_health_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(PORTFOLIO_UPDATED)
    async def handle_portfolio_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    async def run_strategy_evaluation(
        self,
        *,
        strategy_id: str,
        parameters: Mapping[str, Any],
        event_windows: Sequence[Sequence[EventEnvelope]],
    ) -> StrategyEvaluationResult:
        service = self._ensure_service()
        result = service.evaluate_strategy(
            strategy_id=strategy_id,
            parameters=parameters,
            event_windows=event_windows,
        )
        self._research_runs += 1
        self._experiment_ids.append(result.run_id)
        await self.publish_strategy_evaluated(
            StrategyEvaluatedPayload(
                run_id=result.run_id,
                strategy_id=result.strategy_id,
                evaluated_at=result.evaluated_at,
                parameters=result.parameters,
                metrics=result.metrics,
                robustness_score=result.robustness_score,
                warnings=result.warnings,
                status=result.status,
                metadata=result.metadata or {},
                source_event_type=str(STRATEGY_EVALUATED),
            )
        )
        return result

    async def run_parameter_sweep(
        self,
        *,
        strategy_id: str,
        parameter_grid: Mapping[str, Sequence[Any]],
        event_windows: Sequence[Sequence[EventEnvelope]],
    ) -> OptimizationResult:
        service = self._ensure_service()
        result = service.run_parameter_sweep(
            strategy_id=strategy_id,
            parameter_grid=parameter_grid,
            event_windows=event_windows,
        )
        self._research_runs += 1
        self._experiment_ids.append(result.run_id)
        await self.publish_optimization_completed(
            OptimizationCompletedPayload(
                run_id=result.run_id,
                strategy_id=result.strategy_id,
                completed_at=result.completed_at,
                parameter_rankings=result.parameter_rankings,
                best_parameters=result.best_parameters,
                robustness_score=result.robustness_score,
                overfitting_risk=result.overfitting_risk,
                warnings=result.warnings,
                status=result.status,
                metadata=result.metadata or {},
                source_event_type=str(OPTIMIZATION_COMPLETED),
            )
        )
        return result

    async def run_strategy_laboratory(
        self,
        *,
        inputs: Sequence[StrategyResearchInput],
        split: ResearchSplit,
        initial_capital: float = 100_000.0,
        execution_stress_bps: float = 5.0,
        seed: int = 0,
        walk_forward_splits: Sequence[ResearchSplit] = (),
    ) -> StrategyLaboratoryReport:
        """Run isolated historical research without publishing to execution services."""
        return self._ensure_service().evaluate_laboratory(
            inputs=inputs,
            split=split,
            initial_capital=initial_capital,
            execution_stress_bps=execution_stress_bps,
            seed=seed,
            walk_forward_splits=walk_forward_splits,
        )

    async def run_research_report(
        self,
        *,
        strategy_id: str,
        report: ResearchReport,
    ) -> ResearchReport:
        self._research_runs += 1
        self._experiment_ids.append(report.run_id)
        await self.publish_research_completed(
            ResearchCompletedPayload(
                run_id=report.run_id,
                strategy_id=report.strategy_id,
                completed_at=report.completed_at,
                summary=report.summary,
                distributions=report.distributions,
                warnings=report.warnings,
                status=report.status,
                metadata=report.metadata or {},
                source_event_type=str(RESEARCH_COMPLETED),
            )
        )
        return report

    async def publish_strategy_evaluated(self, payload: StrategyEvaluatedPayload) -> None:
        await self.publish(STRATEGY_EVALUATED, payload=payload.to_payload())

    async def publish_optimization_completed(self, payload: OptimizationCompletedPayload) -> None:
        await self.publish(OPTIMIZATION_COMPLETED, payload=payload.to_payload())

    async def publish_research_completed(self, payload: ResearchCompletedPayload) -> None:
        await self.publish(RESEARCH_COMPLETED, payload=payload.to_payload())

    async def export_experiment_results(
        self,
        report: ResearchReport,
        *,
        export_format: str,
        destination: str | None = None,
    ) -> str:
        if self._service is None:
            raise RuntimeError("Research engine service is not configured.")
        exported = self._service.export_result(report, export_format=export_format)
        if destination is not None:
            return f"{destination}:{exported}"
        return exported

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._service is None:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Quantitative research engine is configured but not ready.",
            )
        if self._research_runs == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Quantitative research engine is running before any research experiments.",
                details={"event_counts": dict(self._event_counts)},
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Quantitative research engine has executed research experiments.",
            details={
                "research_runs": self._research_runs,
                "event_counts": dict(self._event_counts),
                "last_experiment": self._experiment_ids[-1] if self._experiment_ids else None,
            },
        )

    def _ensure_service(self) -> QuantitativeResearchService:
        if self._service is None:
            raise RuntimeError("Quantitative research service is not configured.")
        return self._service

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def create_plugin() -> QuantitativeResearchEnginePlugin:
    return QuantitativeResearchEnginePlugin()
