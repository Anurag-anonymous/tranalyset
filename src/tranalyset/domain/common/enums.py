from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    EQUITY = "equity"
    FOREX = "forex"
    CRYPTO = "crypto"
    FUTURE = "future"
    OPTION = "option"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class MarketState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    HALTED = "halted"


class MarketRegime(StrEnum):
    BULL_TREND = "Bull Trend"
    BEAR_TREND = "Bear Trend"
    SIDEWAYS = "Sideways"
    RANGE_BOUND = "Range Bound"
    BREAKOUT = "Breakout"
    BREAKDOWN = "Breakdown"
    HIGH_VOLATILITY = "High Volatility"
    LOW_VOLATILITY = "Low Volatility"
