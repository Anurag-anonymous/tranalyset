"""Market data models for the repository bounded context."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Sequence


class Timeframe(StrEnum):
    TICK = "tick"
    ONE_SECOND = "1s"
    FIVE_SECONDS = "5s"
    ONE_MINUTE = "1m"
    THREE_MINUTES = "3m"
    FIVE_MINUTES = "5m"
    TEN_MINUTES = "10m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    TWO_HOURS = "2h"
    FOUR_HOURS = "4h"
    ONE_DAY = "1d"
    ONE_WEEK = "1w"
    ONE_MONTH = "1M"


class MarketSession(StrEnum):
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    POST_MARKET = "post_market"
    AFTER_HOURS = "after_hours"
    EXTENDED = "extended"


@dataclass(frozen=True, slots=True)
class InstrumentMetadata:
    """Static instrument metadata."""
    symbol: str
    exchange: str
    currency: str
    timezone: str
    instrument_type: str = "equity"
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.symbol:
            msg = "Instrument symbol must not be empty."
            raise ValueError(msg)
        if not self.exchange:
            msg = "Exchange must not be empty."
            raise ValueError(msg)
        if not self.currency:
            msg = "Currency must not be empty."
            raise ValueError(msg)
        if not self.timezone:
            msg = "Timezone must not be empty."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TradingSession:
    """Trading session window in UTC."""
    exchange: str
    session_type: MarketSession
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            msg = "Session start must be timezone-aware."
            raise ValueError(msg)
        if self.end.tzinfo is None:
            msg = "Session end must be timezone-aware."
            raise ValueError(msg)
        if self.end <= self.start:
            msg = "Session end must be after start."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MarketCalendar:
    """Market calendar with trading sessions and holidays."""
    exchange: str
    timezone: str
    sessions: Sequence[TradingSession] = field(default_factory=tuple)
    holidays: Sequence[datetime] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.exchange:
            msg = "Exchange must not be empty."
            raise ValueError(msg)
        if not self.timezone:
            msg = "Timezone must not be empty."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """Corporate action record."""
    symbol: str
    exchange: str
    action_type: str  # split, dividend, merger, etc.
    effective_date: datetime
    ratio: float = 1.0
    amount: float = 0.0
    currency: str = "USD"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.effective_date.tzinfo is None:
            msg = "Effective date must be timezone-aware."
            raise ValueError(msg)
        if not self.symbol:
            msg = "Symbol must not be empty."
            raise ValueError(msg)
        if not self.action_type:
            msg = "Action type must not be empty."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Tick:
    """Market tick data."""
    symbol: str
    exchange: str
    timestamp: datetime
    price: float
    size: float
    side: str = "buy"  # buy, sell, unknown
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            msg = "Tick timestamp must be timezone-aware."
            raise ValueError(msg)
        if self.price <= 0:
            msg = "Tick price must be positive."
            raise ValueError(msg)
        if self.size < 0:
            msg = "Tick size must not be negative."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Candle:
    """OHLCV candle data."""
    symbol: str
    exchange: str
    timeframe: Timeframe
    timestamp: datetime  # Candle open time
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            msg = "Candle timestamp must be timezone-aware."
            raise ValueError(msg)
        if self.high < self.low:
            msg = "Candle high must be >= low."
            raise ValueError(msg)
        if self.high < self.open or self.high < self.close:
            msg = "Candle high must be >= open and >= close."
            raise ValueError(msg)
        if self.low > self.open or self.low > self.close:
            msg = "Candle low must be <= open and <= close."
            raise ValueError(msg)
        if self.volume < 0:
            msg = "Volume must not be negative."
            raise ValueError(msg)