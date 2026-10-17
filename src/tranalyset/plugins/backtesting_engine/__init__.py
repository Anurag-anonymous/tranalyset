"""Backtesting engine plugin."""

from tranalyset.plugins.backtesting_engine.contracts import (
    BACKTEST_COMPLETED,
    BACKTEST_PROGRESS_UPDATED,
    BACKTEST_RESULTS_EXPORTED,
    BACKTEST_STARTED,
    BacktestCompletedPayload,
    BacktestProgressUpdatedPayload,
    BacktestResultsExportedPayload,
    BacktestStartedPayload,
)
from tranalyset.plugins.backtesting_engine.metrics import (
    BacktestComparison,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
)
from tranalyset.plugins.backtesting_engine.plugin import BacktestingEnginePlugin, create_plugin

__all__ = [
    "BACKTEST_COMPLETED",
    "BACKTEST_PROGRESS_UPDATED",
    "BACKTEST_RESULTS_EXPORTED",
    "BACKTEST_STARTED",
    "BacktestComparison",
    "BacktestCompletedPayload",
    "BacktestProgressUpdatedPayload",
    "BacktestResult",
    "BacktestResultsExportedPayload",
    "BacktestStartedPayload",
    "BacktestTrade",
    "BacktestingEnginePlugin",
    "EquityPoint",
    "create_plugin",
]
