from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.kernel.event_bus import AsyncEventBus, Subscription
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec, subscribes_to
from tranalyset.plugins.backtesting_engine.contracts import (
    BACKTEST_COMPLETED,
    BACKTEST_PROGRESS_UPDATED,
    BACKTEST_RESULTS_EXPORTED,
    BACKTEST_STARTED,
    PUBLISHED_EVENTS,
    BacktestCompletedPayload,
    BacktestProgressUpdatedPayload,
    BacktestResultsExportedPayload,
    BacktestStartedPayload,
)
from tranalyset.plugins.backtesting_engine.metrics import (
    BacktestComparison,
    BacktestRecorder,
    BacktestResult,
    compare_strategies,
    export_result,
)
from tranalyset.plugins.paper_trading_engine.contracts import (
    PAPER_PORTFOLIO_UPDATED,
    PAPER_TRADE_CLOSED,
    PAPER_TRADE_UPDATED,
)


class BacktestingEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="backtesting_engine",
                name="Backtesting Engine",
                version="1.0.0",
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("initial_capital", float, required=False, default=100_000.0),
                    ConfigurationField("progress_interval", int, required=False, default=100),
                )
            ),
        )
        self._recorder = BacktestRecorder(initial_capital=100_000.0)
        self._event_bus: AsyncEventBus | None = None
        self._progress_interval = 100
        self._results: dict[str, BacktestResult] = {}
        self._last_result: BacktestResult | None = None
        self._recording_subscription: Subscription | None = None
        self._event_counts = {
            str(PAPER_TRADE_UPDATED): 0,
            str(PAPER_TRADE_CLOSED): 0,
            str(PAPER_PORTFOLIO_UPDATED): 0,
        }

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(BacktestingEnginePlugin, self)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Backtesting engine plugin publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        initial_capital = self.configuration.values["initial_capital"]
        self._progress_interval = self.configuration.values["progress_interval"]
        self._recorder = BacktestRecorder(initial_capital=initial_capital)
        if self._context is not None:
            resolved = self._context.resolve(AsyncEventBus)
            self._event_bus = resolved if isinstance(resolved, AsyncEventBus) else None

    @subscribes_to(PAPER_TRADE_UPDATED)
    async def handle_paper_trade_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(PAPER_TRADE_CLOSED)
    async def handle_paper_trade_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    @subscribes_to(PAPER_PORTFOLIO_UPDATED)
    async def handle_paper_portfolio_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)

    async def run_historical_replay(
        self,
        events: Sequence[EventEnvelope],
        *,
        strategy_id: str,
        run_id: str | None = None,
    ) -> BacktestResult:
        return await self._run_replay(
            events,
            strategy_id=strategy_id,
            run_id=run_id,
            mode="historical_replay",
        )

    async def run_event_replay(
        self,
        events: Sequence[EventEnvelope],
        *,
        strategy_id: str,
        run_id: str | None = None,
    ) -> BacktestResult:
        return await self._run_replay(
            events,
            strategy_id=strategy_id,
            run_id=run_id,
            mode="event_replay",
        )

    async def run_walk_forward(
        self,
        event_windows: Sequence[Sequence[EventEnvelope]],
        *,
        strategy_id: str,
    ) -> tuple[BacktestResult, ...]:
        results: list[BacktestResult] = []
        for index, window in enumerate(event_windows, start=1):
            result = await self._run_replay(
                window,
                strategy_id=strategy_id,
                run_id=f"{strategy_id}:walk-forward:{index}",
                mode="walk_forward",
            )
            results.append(result)
        return tuple(results)

    def compare_strategies(self, results: Sequence[BacktestResult]) -> BacktestComparison:
        return compare_strategies(results)

    async def export_backtest_result(
        self,
        result: BacktestResult,
        *,
        export_format: Literal["json", "csv"],
        destination: str | None = None,
    ) -> str:
        exported = export_result(result, export_format)
        await self.publish_results_exported(
            BacktestResultsExportedPayload(
                run_id=result.run_id,
                strategy_id=result.strategy_id,
                exported_at=datetime.now(UTC),
                export_format=export_format,
                destination=destination,
            )
        )
        return exported

    async def publish_backtest_started(self, payload: BacktestStartedPayload) -> None:
        await self.publish(BACKTEST_STARTED, payload=payload.to_payload())

    async def publish_backtest_progress_updated(self, payload: BacktestProgressUpdatedPayload) -> None:
        await self.publish(BACKTEST_PROGRESS_UPDATED, payload=payload.to_payload())

    async def publish_backtest_completed(self, payload: BacktestCompletedPayload) -> None:
        await self.publish(BACKTEST_COMPLETED, payload=payload.to_payload())

    async def publish_results_exported(self, payload: BacktestResultsExportedPayload) -> None:
        await self.publish(BACKTEST_RESULTS_EXPORTED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._last_result is None:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Backtesting engine is running before a completed backtest.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Backtesting engine has completed at least one replay.",
            details={
                **self._event_counts,
                "completed_runs": len(self._results),
                "last_run_id": self._last_result.run_id,
                "last_trade_count": len(self._last_result.trades),
                "last_drawdown": self._last_result.drawdown,
            },
        )

    def result(self, run_id: str) -> BacktestResult:
        return self._results[run_id]

    def results(self) -> tuple[BacktestResult, ...]:
        return tuple(self._results.values())

    async def _run_replay(
        self,
        events: Sequence[EventEnvelope],
        *,
        strategy_id: str,
        run_id: str | None,
        mode: str,
    ) -> BacktestResult:
        if self._context is None or self._event_bus is None:
            msg = "Backtesting replay requires a running microkernel context."
            raise RuntimeError(msg)
        actual_run_id = run_id or str(uuid4())
        ordered_events = tuple(sorted(events, key=lambda event: event.timestamp))
        started_at = ordered_events[0].timestamp if ordered_events else datetime.now(UTC)
        self._recorder.reset()
        await self._event_bus.wait_until_idle()

        async def record(event: EventEnvelope) -> None:
            self._recorder.record(event)

        self._recording_subscription = await self._event_bus.subscribe_topic("#", record)
        try:
            await self.publish_backtest_started(BacktestStartedPayload(run_id=actual_run_id, strategy_id=strategy_id, mode=mode, started_at=started_at, event_count=len(ordered_events)))
            await self._event_bus.wait_until_idle()
            for index, event in enumerate(ordered_events, start=1):
                await self._context.publish(event)
                await self._event_bus.wait_until_idle()
                if self._should_publish_progress(index, len(ordered_events)):
                    await self.publish_backtest_progress_updated(BacktestProgressUpdatedPayload(run_id=actual_run_id, strategy_id=strategy_id, mode=mode, occurred_at=event.timestamp, events_replayed=index, event_count=len(ordered_events)))
                    await self._event_bus.wait_until_idle()
            completed_at = ordered_events[-1].timestamp if ordered_events else started_at
            result = self._recorder.build_result(run_id=actual_run_id, strategy_id=strategy_id, mode=mode, started_at=started_at, completed_at=completed_at)
            self._results[actual_run_id] = result
            self._last_result = result
            await self.publish_backtest_completed(BacktestCompletedPayload(run_id=actual_run_id, strategy_id=strategy_id, mode=mode, completed_at=completed_at, metrics=result.metrics_payload(), equity_curve=tuple(point.to_payload() for point in result.equity_curve), daily_returns=result.daily_returns, monthly_returns=result.monthly_returns))
            await self._event_bus.wait_until_idle()
            return result
        finally:
            if self._recording_subscription is not None:
                await self._event_bus.unsubscribe(self._recording_subscription)
                self._recording_subscription = None

    def _should_publish_progress(self, index: int, event_count: int) -> bool:
        if event_count == 0:
            return False
        if index == event_count:
            return True
        return self._progress_interval > 0 and index % self._progress_interval == 0

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def create_plugin() -> BacktestingEnginePlugin:
    return BacktestingEnginePlugin()
