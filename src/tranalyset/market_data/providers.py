"""Historical Data Provider abstraction.

Allows multiple historical data sources (CSV, Parquet, Database, Broker APIs, cloud providers)
to feed the Market Data Repository through a uniform interface.
"""
from __future__ import annotations

import asyncio
import csv
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tranalyset.market_data.models import (
    Candle,
    CorporateAction,
    InstrumentMetadata,
    MarketCalendar,
    Tick,
    Timeframe,
)


class HistoricalDataProvider(ABC):
    """Abstract interface for loading historical market data.

    Implementations may read from CSV files, Parquet datasets, databases,
    broker APIs, or future cloud providers. Providers MUST NOT know about
    plugins or indicators. They return immutable domain models only.
    """

    @abstractmethod
    async def load_ticks(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[Tick]:
        """Load tick data for a symbol/exchange within a time range."""
        raise NotImplementedError

    @abstractmethod
    async def load_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Load OHLCV candles for a symbol/exchange/timeframe within a time range."""
        raise NotImplementedError

    @abstractmethod
    async def load_sessions(
        self,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[MarketCalendar]:
        """Load trading sessions and market calendars for an exchange."""
        raise NotImplementedError

    @abstractmethod
    async def load_metadata(
        self,
        symbol: str | None = None,
        exchange: str | None = None,
    ) -> Sequence[InstrumentMetadata]:
        """Load instrument metadata. Optional filters by symbol and/or exchange."""
        raise NotImplementedError

    @abstractmethod
    async def load_corporate_actions(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[CorporateAction]:
        """Load corporate actions for a symbol/exchange within a time range."""
        raise NotImplementedError


class InMemoryHistoricalDataProvider(HistoricalDataProvider):
    """In-memory provider for testing and synthetic data generation.

    Stores data in memory and returns immutable sequences.
    """

    def __init__(self) -> None:
        self._ticks: list[Tick] = []
        self._candles: list[Candle] = []
        self._sessions: list[MarketCalendar] = []
        self._metadata: list[InstrumentMetadata] = []
        self._corporate_actions: list[CorporateAction] = []

    def add_tick(self, tick: Tick) -> None:
        self._ticks.append(tick)

    def add_candle(self, candle: Candle) -> None:
        self._candles.append(candle)

    def add_session(self, session: MarketCalendar) -> None:
        self._sessions.append(session)

    def add_metadata(self, metadata: InstrumentMetadata) -> None:
        self._metadata.append(metadata)

    def add_corporate_action(self, action: CorporateAction) -> None:
        self._corporate_actions.append(action)

    async def load_ticks(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[Tick]:
        result = [
            tick
            for tick in self._ticks
            if tick.symbol == symbol and tick.exchange == exchange and start <= tick.timestamp <= end
        ]
        return tuple(result)

    async def load_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        result = [
            candle
            for candle in self._candles
            if candle.symbol == symbol and candle.exchange == exchange and candle.timeframe == timeframe and start <= candle.timestamp <= end
        ]
        return tuple(result)

    async def load_sessions(
        self,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[MarketCalendar]:
        result = [
            session for session in self._sessions if session.exchange == exchange
        ]
        return tuple(result)

    async def load_metadata(
        self,
        symbol: str | None = None,
        exchange: str | None = None,
    ) -> Sequence[InstrumentMetadata]:
        result = list(self._metadata)
        if symbol is not None:
            result = [m for m in result if m.symbol == symbol]
        if exchange is not None:
            result = [m for m in result if m.exchange == exchange]
        return tuple(result)

    async def load_corporate_actions(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[CorporateAction]:
        result = [
            action
            for action in self._corporate_actions
            if action.symbol == symbol and action.exchange == exchange and start <= action.effective_date <= end
        ]
        return tuple(result)


class CSVHistoricalDataProvider(HistoricalDataProvider):
    """Load validated OHLCV candles from a single CSV file."""

    def __init__(
        self,
        base_path: str,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        timezone: str = "UTC",
        timestamp_column: str = "timestamp",
        open_column: str = "open",
        high_column: str = "high",
        low_column: str = "low",
        close_column: str = "close",
        volume_column: str = "volume",
    ) -> None:
        self._base_path = Path(base_path)
        self._symbol = _required_setting(symbol, "symbol")
        self._exchange = _required_setting(exchange, "exchange")
        self._timeframe = timeframe
        self._timezone = ZoneInfo(timezone)
        self._columns = {
            "timestamp": timestamp_column,
            "open": open_column,
            "high": high_column,
            "low": low_column,
            "close": close_column,
            "volume": volume_column,
        }

    async def load_ticks(self, symbol: str, exchange: str, start: datetime, end: datetime) -> Sequence[Tick]:
        return ()

    async def load_candles(self, symbol: str, exchange: str, timeframe: Timeframe, start: datetime, end: datetime) -> Sequence[Candle]:
        _validate_range(start, end)
        if (symbol, exchange, timeframe) != (self._symbol, self._exchange, self._timeframe):
            return ()
        rows = await asyncio.to_thread(self._read_candles)
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        return tuple(candle for candle in rows if start_utc <= candle.timestamp <= end_utc)

    async def load_sessions(self, exchange: str, start: datetime, end: datetime) -> Sequence[MarketCalendar]:
        return ()

    async def load_metadata(self, symbol: str | None = None, exchange: str | None = None) -> Sequence[InstrumentMetadata]:
        return ()

    async def load_corporate_actions(self, symbol: str, exchange: str, start: datetime, end: datetime) -> Sequence[CorporateAction]:
        return ()

    def _read_candles(self) -> tuple[Candle, ...]:
        candles: list[Candle] = []
        previous_timestamp: datetime | None = None
        try:
            source = self._base_path.open("r", encoding="utf-8-sig", newline="")
        except OSError as error:
            raise OSError(f"Unable to open candle CSV {self._base_path}: {error}") from error

        with source:
            reader = csv.DictReader(source)
            headers = reader.fieldnames
            if headers is None:
                raise ValueError(f"Candle CSV {self._base_path} is empty or missing a header.")
            missing_columns = set(self._columns.values()) - set(headers)
            if missing_columns:
                raise ValueError(
                    f"Candle CSV {self._base_path} is missing columns: "
                    f"{', '.join(sorted(missing_columns))}."
                )

            for row_number, row in enumerate(reader, start=2):
                try:
                    raw_timestamp = row[self._columns["timestamp"]]
                    if raw_timestamp is None:
                        raise ValueError("timestamp is empty")
                    timestamp = datetime.fromisoformat(raw_timestamp.strip())
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=self._timezone)
                    timestamp = timestamp.astimezone(UTC)
                    values = {
                        name: float(row[column])
                        for name, column in self._columns.items()
                        if name != "timestamp"
                    }
                    if not all(math.isfinite(value) for value in values.values()):
                        raise ValueError("OHLCV values must be finite")
                    if min(values["open"], values["high"], values["low"], values["close"]) <= 0:
                        raise ValueError("OHLC prices must be positive")
                    if values["volume"] < 0:
                        raise ValueError("volume must not be negative")
                    if (
                        values["high"] < max(values["open"], values["low"], values["close"])
                        or values["low"] > min(values["open"], values["high"], values["close"])
                    ):
                        raise ValueError("OHLC values violate candle high/low bounds")
                    if previous_timestamp is not None and timestamp <= previous_timestamp:
                        raise ValueError("timestamps must be strictly increasing and unique")
                    candles.append(
                        Candle(
                            symbol=self._symbol,
                            exchange=self._exchange,
                            timeframe=self._timeframe,
                            timestamp=timestamp,
                            open=values["open"],
                            high=values["high"],
                            low=values["low"],
                            close=values["close"],
                            volume=values["volume"],
                        )
                    )
                    previous_timestamp = timestamp
                except (TypeError, ValueError, KeyError) as error:
                    raise ValueError(
                        f"Invalid candle CSV row {row_number} in {self._base_path}: {error}"
                    ) from error

        if not candles:
            raise ValueError(f"Candle CSV {self._base_path} contains no candle rows.")
        return tuple(candles)


def _required_setting(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    return value.strip()


def _validate_range(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Historical data date range must be timezone-aware.")
    if end < start:
        raise ValueError("Historical data end must be on or after start.")