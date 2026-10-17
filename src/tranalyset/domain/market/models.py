from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from tranalyset.domain.common import AssetClass
from tranalyset.domain.common.value_objects import (
    Exchange,
    Liquidity,
    Price,
    Quantity,
    Spread,
    Symbol,
    Timeframe as BaseTimeframe,
)


def _require_aware(timestamp: datetime, field_name: str) -> None:
    if timestamp.tzinfo is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: Symbol
    exchange: Exchange
    asset_class: AssetClass
    pip_size: Decimal | int | float | str = Decimal("0.01")
    multiplier: Decimal | int | float | str = Decimal("1")

    def __post_init__(self) -> None:
        pip_size = float(self.pip_size)
        multiplier = float(self.multiplier)
        if pip_size <= 0:
            msg = "Pip size must be greater than zero."
            raise ValueError(msg)
        if multiplier <= 0:
            msg = "Multiplier must be greater than zero."
            raise ValueError(msg)
        object.__setattr__(self, "pip_size", pip_size)
        object.__setattr__(self, "multiplier", multiplier)

    @property
    def instrument_id(self) -> str:
        return f"{self.symbol.value}:{self.exchange.value}"


@dataclass(frozen=True, slots=True)
class PriceQuote:
    instrument: Instrument
    timestamp: datetime
    bid: Price
    ask: Price
    last: Price

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "PriceQuote timestamp")
        if self.bid.value > self.ask.value and self.ask.value > 0:
            msg = "Bid price must not be greater than Ask price."
            raise ValueError(msg)

    @property
    def mid(self) -> Price:
        return Price((self.bid.value + self.ask.value) / Decimal("2"))


Quote = PriceQuote


@dataclass(frozen=True, slots=True)
class TradingSession:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware(self.start, "TradingSession start")
        _require_aware(self.end, "TradingSession end")
        if self.start >= self.end:
            msg = "Trading session start must be before end."
            raise ValueError(msg)

    def is_open(self, timestamp: datetime) -> bool:
        _require_aware(timestamp, "timestamp")
        return self.start <= timestamp <= self.end


@dataclass(frozen=True, slots=True)
class MarketCalendar:
    sessions: Mapping[datetime, TradingSession] = field(default_factory=dict)

    def __post_init__(self) -> None:
        frozen = MappingProxyType(dict(self.sessions))
        for timestamp, session in frozen.items():
            _require_aware(timestamp, "MarketCalendar session key")
            if timestamp != session.start:
                msg = "Session key must match session start time."
                raise ValueError(msg)
        object.__setattr__(self, "sessions", frozen)

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.sessions.items(), key=lambda item: item[0])))

    def is_open(self, timestamp: datetime) -> bool:
        _require_aware(timestamp, "timestamp")
        return any(session.is_open(timestamp) for session in self.sessions.values())


@dataclass(frozen=True, slots=True)
class Tick:
    instrument: Instrument
    timestamp: datetime
    price: Price
    volume: Quantity

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "Tick timestamp")


@dataclass(frozen=True, slots=True)
class OrderBook:
    instrument: Instrument
    bids: Mapping[Price, Quantity] = field(default_factory=dict)
    asks: Mapping[Price, Quantity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "bids", MappingProxyType(dict(self.bids)))
        object.__setattr__(self, "asks", MappingProxyType(dict(self.asks)))
        if self.best_bid is not None and self.best_ask is not None and self.best_bid.value > self.best_ask.value:
            msg = "Best bid must not exceed best ask."
            raise ValueError(msg)

    def __hash__(self) -> int:
        bids = tuple(sorted(self.bids.items(), key=lambda item: item[0].value))
        asks = tuple(sorted(self.asks.items(), key=lambda item: item[0].value))
        return hash((self.instrument, bids, asks))

    @property
    def best_bid(self) -> Price | None:
        return max(self.bids.keys(), key=lambda price: price.value, default=None)

    @property
    def best_ask(self) -> Price | None:
        return min(self.asks.keys(), key=lambda price: price.value, default=None)

    def spread(self) -> Price | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def spread_value(self) -> Spread | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return Spread(self.best_ask.value - self.best_bid.value)


@dataclass(frozen=True, slots=True)
class Timeframe(BaseTimeframe):
    def aggregate(self, ticks: Iterable[Tick]) -> list[Candle]:
        return list(aggregate_ticks(self, ticks))


@dataclass(frozen=True, slots=True)
class Candle:
    instrument: Instrument
    timestamp: datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Quantity
    timeframe: Timeframe = Timeframe(60)

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "Candle timestamp")
        if self.high.value < self.low.value:
            msg = "High must be >= low."
            raise ValueError(msg)
        if not self.low.value <= self.open.value <= self.high.value:
            msg = "Open must be within candle high/low range."
            raise ValueError(msg)
        if not self.low.value <= self.close.value <= self.high.value:
            msg = "Close must be within candle high/low range."
            raise ValueError(msg)

    @property
    def range(self) -> Price:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class MarketLiquidity:
    instrument: Instrument
    timestamp: datetime
    bid_liquidity: Liquidity
    ask_liquidity: Liquidity

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "MarketLiquidity timestamp")

    @property
    def total(self) -> Liquidity:
        return Liquidity(self.bid_liquidity.value + self.ask_liquidity.value)


def aggregate_ticks(timeframe: Timeframe, ticks: Iterable[Tick]) -> tuple[Candle, ...]:
    groups: dict[int, list[Tick]] = {}
    for tick in ticks:
        key = int(tick.timestamp.timestamp() // timeframe.seconds)
        groups.setdefault(key, []).append(tick)

    candles: list[Candle] = []
    for key, group in sorted(groups.items()):
        ordered = tuple(sorted(group, key=lambda tick: tick.timestamp))
        total_volume = sum((tick.volume.value for tick in ordered), Decimal("0"))
        candles.append(
            Candle(
                instrument=ordered[0].instrument,
                timestamp=datetime.fromtimestamp(key * timeframe.seconds, tz=UTC),
                open=ordered[0].price,
                high=max((tick.price for tick in ordered), key=lambda price: price.value),
                low=min((tick.price for tick in ordered), key=lambda price: price.value),
                close=ordered[-1].price,
                volume=Quantity(total_volume),
                timeframe=timeframe,
            )
        )
    return tuple(candles)
