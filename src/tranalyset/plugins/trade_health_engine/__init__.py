"""Trade health engine plugin."""

from tranalyset.plugins.trade_health_engine.contracts import (
    CONFIDENCE_CHANGED,
    TRADE_STRENGTH_CHANGED,
    ConfidenceChangedPayload,
    TradeHealthUpdatedPayload,
    TradeStrengthChangedPayload,
)
from tranalyset.plugins.trade_health_engine.plugin import TradeHealthEnginePlugin, create_plugin
from tranalyset.plugins.trade_manager.contracts import TRADE_HEALTH_UPDATED

__all__ = [
    "CONFIDENCE_CHANGED",
    "TRADE_HEALTH_UPDATED",
    "TRADE_STRENGTH_CHANGED",
    "ConfidenceChangedPayload",
    "TradeHealthEnginePlugin",
    "TradeHealthUpdatedPayload",
    "TradeStrengthChangedPayload",
    "create_plugin",
]
