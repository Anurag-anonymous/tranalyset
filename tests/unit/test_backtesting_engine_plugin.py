from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
import json

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.backtesting_engine import (
    BACKTEST_COMPLETED,
    BACKTEST_PROGRESS_UPDATED,
    BACKTEST_RESULTS_EXPORTED,
    BACKTEST_STARTED,
    BacktestCompletedPayload,
    BacktestProgressUpdatedPayload,
    BacktestResultsExportedPayload,
    BacktestStartedPayload,
    BacktestingEnginePlugin,
)
from tranalyset.plugins.backtesting_engine.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.backtesting_engine.metrics import BacktestRecorder, compare_strategies
from tranalyset.plugins.paper_trading_engine import (
    PAPER_EXECUTION_RECORDED,
    PAPER_PORTFOLIO_UPDATED,
    PAPER_TRADE_CLOSED,
    PAPER_TRADE_UPDATED,
    PaperTradingEnginePlugin,
)
from tranalyset.plugins.risk import RISK_APPROVED, TRADE_REQUESTED
from tranalyset.plugins.trade_manager import TRADE_CLOSED, TRADE_OPENED, TradeManagerPlugin


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "backtesting_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "backtesting_engine"
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
    *,
    config: Mapping[str, object] | None = None,
) -> tuple[BacktestingEnginePlugin, CapturingContext]:
    plugin = BacktestingEnginePlugin()
    context = CapturingContext(config=config)
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    return plugin, context


def _event(
    timestamp: datetime,
    event_type: EventType,
    payload: Mapping[str, object],
    *,
    source: str = "historical_fixture",
) -> EventEnvelope:
    return EventEnvelope(
        timestamp=timestamp,
        source=source,
        event_type=event_type,
        payload=payload,
        metadata={"replay": True},
    )


def _trade_opened(timestamp: datetime, trade_id: str, entry_price: float) -> EventEnvelope:
    return _event(
        timestamp,
        RISK_APPROVED,
        {
            "trade_id": trade_id,
            "symbol": "AAPL",
            "quantity": 1.0,
            "entry_price": entry_price,
            "order_type": "market",
            "source_event_type": str(TRADE_REQUESTED),
            "reason": "historical replay approval",
            "evidence": {},
        },
        source="risk",
    )


def _trade_closed(timestamp: datetime, trade_id: str, exit_price: float) -> EventEnvelope:
    return _event(
        timestamp,
        TRADE_CLOSED,
        {
            "trade_id": trade_id,
            "symbol": "AAPL",
            "exit_price": exit_price,
            "reason": "historical replay close",
        },
        source="trade_manager",
    )


def test_backtesting_engine_metadata_is_versioned() -> None:
    plugin = BacktestingEnginePlugin()

    assert plugin.metadata.plugin_id == "backtesting_engine"
    assert plugin.metadata.name == "Backtesting Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_backtesting_engine_installs_published_event_contracts() -> None:
    plugin = BacktestingEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_backtesting_engine_subscribes_to_paper_execution_events() -> None:
    plugin = BacktestingEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {
        PAPER_TRADE_UPDATED,
        PAPER_TRADE_CLOSED,
        PAPER_PORTFOLIO_UPDATED,
    }


async def test_backtesting_engine_publishes_required_event_methods() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_backtest_started(
        BacktestStartedPayload(
            run_id="run-1",
            strategy_id="strategy-a",
            mode="historical_replay",
            started_at=timestamp,
            event_count=10,
        )
    )
    await plugin.publish_backtest_progress_updated(
        BacktestProgressUpdatedPayload(
            run_id="run-1",
            strategy_id="strategy-a",
            mode="historical_replay",
            occurred_at=timestamp,
            events_replayed=5,
            event_count=10,
        )
    )
    await plugin.publish_backtest_completed(
        BacktestCompletedPayload(
            run_id="run-1",
            strategy_id="strategy-a",
            mode="historical_replay",
            completed_at=timestamp,
            metrics={"win_rate": 1.0},
            equity_curve=(),
            daily_returns={},
            monthly_returns={},
        )
    )
    await plugin.publish_results_exported(
        BacktestResultsExportedPayload(
            run_id="run-1",
            strategy_id="strategy-a",
            exported_at=timestamp,
            export_format="json",
        )
    )

    assert [event.event_type for event in context.published] == [
        BACKTEST_STARTED,
        BACKTEST_PROGRESS_UPDATED,
        BACKTEST_COMPLETED,
        BACKTEST_RESULTS_EXPORTED,
    ]
    assert all(event.source == "backtesting_engine" for event in context.published)


async def test_backtesting_engine_replays_events_through_live_kernel_plugins() -> None:
    backtesting = BacktestingEnginePlugin()
    completed: list[EventEnvelope] = []
    progress: list[EventEnvelope] = []
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader(
            [TradeManagerPlugin(), PaperTradingEnginePlugin(), backtesting]
        ),
        plugin_config={
            "paper_trading_engine": {
                "initial_cash": 10_000.0,
                "commission_rate": 0.0,
                "slippage_rate": 0.0,
            },
            "backtesting_engine": {"initial_capital": 10_000.0, "progress_interval": 2},
        },
    )

    async def collect_completed(event: EventEnvelope) -> None:
        completed.append(event)

    async def collect_progress(event: EventEnvelope) -> None:
        progress.append(event)

    await kernel.event_bus.subscribe(BACKTEST_COMPLETED, collect_completed)
    await kernel.event_bus.subscribe(BACKTEST_PROGRESS_UPDATED, collect_progress)
    await kernel.start()
    start = _now()
    result = await backtesting.run_historical_replay(
        (
            _trade_closed(start + timedelta(minutes=2), "trade-1", 110.0),
            _trade_opened(start, "trade-1", 100.0),
            _trade_opened(start + timedelta(days=1), "trade-2", 100.0),
            _trade_closed(start + timedelta(days=1, minutes=2), "trade-2", 90.0),
        ),
        strategy_id="strategy-a",
        run_id="run-1",
    )
    health = await kernel.plugin_health("backtesting_engine")
    await kernel.stop()

    assert result.run_id == "run-1"
    assert result.mode == "historical_replay"
    assert len(result.trades) == 2
    assert result.win_rate == 0.5
    assert result.profit_factor == 1.0
    assert result.maximum_consecutive_win == 1
    assert result.maximum_consecutive_loss == 1
    assert result.drawdown > 0
    assert set(result.daily_returns) == {"2026-01-01", "2026-01-02"}
    assert len(result.equity_curve) >= 4
    assert completed[-1].payload["run_id"] == "run-1"
    assert [event.payload["events_replayed"] for event in progress] == [2, 4]
    assert health.status is HealthStatus.HEALTHY
    assert health.details["completed_runs"] == 1


async def test_event_replay_walk_forward_strategy_comparison_and_export() -> None:
    backtesting = BacktestingEnginePlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader(
            [TradeManagerPlugin(), PaperTradingEnginePlugin(), backtesting]
        ),
        plugin_config={
            "paper_trading_engine": {
                "initial_cash": 20_000.0,
                "commission_rate": 0.0,
                "slippage_rate": 0.0,
            },
            "backtesting_engine": {"initial_capital": 20_000.0, "progress_interval": 0},
        },
    )
    exported_events: list[EventEnvelope] = []

    async def collect_exported(event: EventEnvelope) -> None:
        exported_events.append(event)

    await kernel.event_bus.subscribe(BACKTEST_RESULTS_EXPORTED, collect_exported)
    await kernel.start()
    start = _now()
    result = await backtesting.run_event_replay(
        (
            _trade_opened(start, "trade-1", 100.0),
            _trade_closed(start + timedelta(minutes=1), "trade-1", 120.0),
        ),
        strategy_id="strategy-a",
        run_id="event-run",
    )
    walk_forward = await backtesting.run_walk_forward(
        (
            (
                _trade_opened(start + timedelta(days=1), "trade-2", 100.0),
                _trade_closed(start + timedelta(days=1, minutes=1), "trade-2", 115.0),
            ),
            (
                _trade_opened(start + timedelta(days=2), "trade-3", 100.0),
                _trade_closed(start + timedelta(days=2, minutes=1), "trade-3", 90.0),
            ),
        ),
        strategy_id="strategy-a",
    )
    comparison = backtesting.compare_strategies((result, *walk_forward))
    json_export = await backtesting.export_backtest_result(result, export_format="json")
    csv_export = await backtesting.export_backtest_result(result, export_format="csv")
    await kernel.stop()

    assert result.mode == "event_replay"
    assert [item.mode for item in walk_forward] == ["walk_forward", "walk_forward"]
    assert len(comparison.results) == 3
    assert comparison.ranking[0]["rank"] == 1
    assert json.loads(json_export)["run_id"] == "event-run"
    assert "section,key,value" in csv_export
    assert [event.event_type for event in exported_events] == [
        BACKTEST_RESULTS_EXPORTED,
        BACKTEST_RESULTS_EXPORTED,
    ]


async def test_backtesting_records_only_chronological_evidence_without_future_visibility() -> None:
    backtesting = BacktestingEnginePlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader(
            [TradeManagerPlugin(), PaperTradingEnginePlugin(), backtesting]
        ),
        plugin_config={"paper_trading_engine": {"commission_rate": 0.0, "slippage_rate": 0.0}},
    )
    await kernel.start()
    start = _now()
    result = await backtesting.run_historical_replay(
        (
            _trade_closed(start + timedelta(minutes=2), "trade-1", 110.0),
            _trade_opened(start, "trade-1", 100.0),
        ),
        strategy_id="causal-strategy",
    )
    await kernel.stop()

    timestamps = [event.timestamp for event in result.evidence]
    assert timestamps == sorted(timestamps)
    first_fill = next(event for event in result.evidence if str(event.event_type) == PAPER_TRADE_UPDATED)
    assert first_fill.timestamp == start
    assert all(event.timestamp <= start + timedelta(minutes=2) for event in result.evidence)


async def test_backtesting_is_deterministic_for_a_fixed_historical_dataset() -> None:
    async def run_once() -> tuple[dict[str, float | int], tuple[tuple[str, float], ...]]:
        backtesting = BacktestingEnginePlugin()
        kernel = Microkernel(
            plugin_loader=StaticPluginLoader(
                [TradeManagerPlugin(), PaperTradingEnginePlugin(), backtesting]
            ),
            plugin_config={"paper_trading_engine": {"commission_rate": 0.001, "slippage_rate": 0.0005}},
        )
        await kernel.start()
        start = _now()
        result = await backtesting.run_historical_replay(
            (_trade_opened(start, "trade-1", 100.0), _trade_closed(start + timedelta(minutes=3), "trade-1", 110.0)),
            strategy_id="deterministic-strategy",
        )
        await kernel.stop()
        return result.metrics_payload(), tuple((trade.trade_id, trade.realized_pnl) for trade in result.trades)

    assert await run_once() == await run_once()


def test_backtest_recorder_generates_required_metrics_from_paper_events() -> None:
    recorder = BacktestRecorder(initial_capital=10_000.0)
    start = _now()

    for event in (
        _event(
            start,
            PAPER_PORTFOLIO_UPDATED,
            {
                "portfolio_id": "paper",
                "occurred_at": start.isoformat(),
                "cash": 10_000.0,
                "equity": 10_000.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "virtual_positions": (),
                "virtual_orders": (),
                "trade_history": (),
            },
            source="paper_trading_engine",
        ),
        _event(
            start + timedelta(days=1),
            PAPER_PORTFOLIO_UPDATED,
            {
                "portfolio_id": "paper",
                "occurred_at": (start + timedelta(days=1)).isoformat(),
                "cash": 10_012.0,
                "equity": 10_012.0,
                "realized_pnl": 12.0,
                "unrealized_pnl": 0.0,
                "virtual_positions": (),
                "virtual_orders": (),
                "trade_history": (),
            },
            source="paper_trading_engine",
        ),
        _event(
            start + timedelta(days=1),
            PAPER_TRADE_CLOSED,
            {
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "occurred_at": (start + timedelta(days=1)).isoformat(),
                "quantity": 1.0,
                "average_price": 100.0,
                "exit_price": 112.0,
                "realized_pnl": 12.0,
                "commissions": 0.0,
                "slippage": 0.0,
                "reason": "closed",
                "risk_amount": 6.0,
            },
            source="paper_trading_engine",
        ),
        _event(
            start + timedelta(days=2),
            PAPER_PORTFOLIO_UPDATED,
            {
                "portfolio_id": "paper",
                "occurred_at": (start + timedelta(days=2)).isoformat(),
                "cash": 10_006.0,
                "equity": 10_006.0,
                "realized_pnl": 6.0,
                "unrealized_pnl": 0.0,
                "virtual_positions": (),
                "virtual_orders": (),
                "trade_history": (),
            },
            source="paper_trading_engine",
        ),
        _event(
            start + timedelta(days=2),
            PAPER_TRADE_CLOSED,
            {
                "trade_id": "trade-2",
                "symbol": "AAPL",
                "occurred_at": (start + timedelta(days=2)).isoformat(),
                "quantity": 1.0,
                "average_price": 100.0,
                "exit_price": 94.0,
                "realized_pnl": -6.0,
                "commissions": 0.0,
                "slippage": 0.0,
                "reason": "closed",
                "risk_amount": 6.0,
            },
            source="paper_trading_engine",
        ),
    ):
        recorder.record(event)

    result = recorder.build_result(
        run_id="run-1",
        strategy_id="strategy-a",
        mode="historical_replay",
        started_at=start,
        completed_at=start + timedelta(days=2),
    )
    comparison = compare_strategies((result,))

    assert result.win_rate == 0.5
    assert result.profit_factor == 2.0
    assert result.average_rr == 0.5
    assert result.drawdown > 0
    assert result.sharpe_ratio != 0
    assert result.total_return == pytest.approx(0.0006)
    assert result.drawdown == pytest.approx(6.0 / 10_012.0)
    assert result.sortino_ratio != 0
    assert result.maximum_consecutive_win == 1
    assert result.maximum_consecutive_loss == 1
    assert set(result.monthly_returns) == {"2026-01"}
    assert comparison.ranking[0]["strategy_id"] == "strategy-a"


def test_backtest_without_trades_or_equity_is_not_ranked() -> None:
    recorder = BacktestRecorder(initial_capital=10_000.0)
    start = _now()
    result = recorder.build_result(
        run_id="empty",
        strategy_id="strategy-empty",
        mode="historical_replay",
        started_at=start,
        completed_at=start + timedelta(days=1),
    )

    assert result.status == "not_evaluated"
    assert result.win_rate is None
    assert result.profit_factor is None
    assert result.drawdown is None
    assert compare_strategies((result,)).ranking == ()


def test_backtest_recorder_rejects_invalid_trade_ledger_values() -> None:
    recorder = BacktestRecorder(initial_capital=10_000.0)
    event = _event(
        _now(),
        PAPER_TRADE_CLOSED,
        {
            "trade_id": "bad-trade",
            "symbol": "AAPL",
            "occurred_at": _now().isoformat(),
            "quantity": 0.0,
            "average_price": 100.0,
            "exit_price": 101.0,
            "realized_pnl": 1.0,
            "commissions": 0.0,
            "slippage": 0.0,
        },
        source="paper_trading_engine",
    )

    with pytest.raises(ValueError, match="quantity must be positive"):
        recorder.record(event)


def test_backtest_recorder_rejects_fill_ledger_that_does_not_reconcile() -> None:
    recorder = BacktestRecorder(initial_capital=10_000.0)
    opened_at = _now()
    closed_at = opened_at + timedelta(minutes=1)
    recorder.record(
        _event(
            opened_at,
            PAPER_EXECUTION_RECORDED,
            {
                "action": "fill",
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "occurred_at": opened_at.isoformat(),
                "evidence": {"quantity": 1.0, "price": 100.0, "commission": 1.0, "slippage": 0.1},
            },
            source="paper_trading_engine",
        )
    )
    recorder.record(
        _event(
            closed_at,
            PAPER_EXECUTION_RECORDED,
            {
                "action": "exit_fill",
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "occurred_at": closed_at.isoformat(),
                "evidence": {
                    "quantity": 1.0,
                    "price": 101.0,
                    "commission": 1.0,
                    "slippage": 0.1,
                    "realized_pnl": 0.0,
                },
            },
            source="paper_trading_engine",
        )
    )
    close = _event(
        closed_at,
        PAPER_TRADE_CLOSED,
        {
            "trade_id": "trade-1",
            "symbol": "AAPL",
            "occurred_at": closed_at.isoformat(),
            "quantity": 1.0,
            "average_price": 100.0,
            "exit_price": 101.0,
            "realized_pnl": 0.0,
            "commissions": 2.0,
            "slippage": 0.2,
        },
        source="paper_trading_engine",
    )

    with pytest.raises(ValueError, match="realized P&L does not reconcile"):
        recorder.record(close)


async def test_backtesting_replay_requires_running_kernel_context() -> None:
    plugin = BacktestingEnginePlugin()

    with pytest.raises(RuntimeError, match="running microkernel"):
        await plugin.run_historical_replay((), strategy_id="strategy-a")


def test_backtesting_payloads_do_not_include_live_broker_fields_or_recommendations() -> None:
    payload = BacktestCompletedPayload(
        run_id="run-1",
        strategy_id="strategy-a",
        mode="historical_replay",
        completed_at=_now(),
        metrics={"win_rate": 1.0},
        equity_curve=(),
        daily_returns={},
        monthly_returns={},
    ).to_payload()

    forbidden = {
        "api_key",
        "broker_account",
        "broker_order_id",
        "entry_recommendation",
        "live_order",
        "recommendation",
    }

    assert forbidden.isdisjoint(payload)
