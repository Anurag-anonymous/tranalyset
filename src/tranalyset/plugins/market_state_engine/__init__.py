"""Market state engine plugin."""

from tranalyset.plugins.market_state_engine.contracts import (
    LIQUIDITY_CHANGED,
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
    MarketRegimeChangedPayload,
    MomentumStateChangedPayload,
    StateChangePayload,
    SUPPORTED_MARKET_REGIMES,
    VolatilityChangedPayload,
)
from tranalyset.plugins.market_state_engine.plugin import MarketStateEnginePlugin, create_plugin

__all__ = [
    "LIQUIDITY_CHANGED",
    "MARKET_REGIME_CHANGED",
    "MOMENTUM_STATE_CHANGED",
    "SUPPORTED_MARKET_REGIMES",
    "TREND_CONFIRMED",
    "TREND_WEAKENED",
    "VOLATILITY_CHANGED",
    "MarketRegimeChangedPayload",
    "MarketStateEnginePlugin",
    "MomentumStateChangedPayload",
    "StateChangePayload",
    "VolatilityChangedPayload",
    "create_plugin",
]
