"""Paper trading engine plugin."""

from tranalyset.plugins.paper_trading_engine.contracts import (
    PAPER_PORTFOLIO_UPDATED,
    PAPER_EXECUTION_RECORDED,
    PAPER_PERFORMANCE_REPORTED,
    PAPER_RISK_LIMIT_ENFORCED,
    PAPER_TRADE_CLOSED,
    PAPER_TRADE_UPDATED,
    PaperPortfolioUpdatedPayload,
    PaperTradeClosedPayload,
    PaperTradeUpdatedPayload,
)
from tranalyset.plugins.paper_trading_engine.plugin import PaperTradingEnginePlugin, create_plugin

__all__ = [
    "PAPER_PORTFOLIO_UPDATED",
    "PAPER_EXECUTION_RECORDED",
    "PAPER_PERFORMANCE_REPORTED",
    "PAPER_RISK_LIMIT_ENFORCED",
    "PAPER_TRADE_CLOSED",
    "PAPER_TRADE_UPDATED",
    "PaperPortfolioUpdatedPayload",
    "PaperTradeClosedPayload",
    "PaperTradeUpdatedPayload",
    "PaperTradingEnginePlugin",
    "create_plugin",
]
