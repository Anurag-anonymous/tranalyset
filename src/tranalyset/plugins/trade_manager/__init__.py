"""Trade manager plugin."""

from tranalyset.plugins.trade_manager.contracts import (
    TRADE_CLOSED,
    TRADE_HEALTH_UPDATED,
    TRADE_MODIFIED,
    TRADE_OPENED,
    TRADE_SIGNAL_GENERATED,
    TRAILING_STOP_UPDATED,
    TradeClosedPayload,
    TradeModifiedPayload,
    TradeOpenedPayload,
    TrailingStopUpdatedPayload,
)
from tranalyset.plugins.trade_manager.plugin import TradeManagerPlugin, create_plugin

__all__ = [
    "TRADE_CLOSED",
    "TRADE_HEALTH_UPDATED",
    "TRADE_MODIFIED",
    "TRADE_OPENED",
    "TRADE_SIGNAL_GENERATED",
    "TRAILING_STOP_UPDATED",
    "TradeClosedPayload",
    "TradeManagerPlugin",
    "TradeModifiedPayload",
    "TradeOpenedPayload",
    "TrailingStopUpdatedPayload",
    "create_plugin",
]
