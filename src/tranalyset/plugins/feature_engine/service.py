from __future__ import annotations

import asyncio
import math
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, MarketCalendar, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.market_data.contracts import TICK_RECEIVED
from tranalyset.plugins.feature_engine.contracts import FeatureSet


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    symbol: str
    exchange: str
    timeframe: Timeframe
    observed_at: datetime
    source_event_type: str
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float

    def __post_init__(self) -> None:
        if not self.symbol:
            msg = "symbol must not be empty."
            raise ValueError(msg)
        if not self.exchange:
            msg = "exchange must not be empty."
            raise ValueError(msg)
        if self.observed_at.tzinfo is None:
            msg = "observed_at must be timezone-aware."
            raise ValueError(msg)
        if self.open_price <= 0 or self.high_price <= 0 or self.low_price <= 0 or self.close_price <= 0:
            msg = "price values must be positive."
            raise ValueError(msg)
        if self.high_price < self.low_price:
            msg = "high_price must be >= low_price."
            raise ValueError(msg)
        if self.low_price > self.open_price or self.open_price > self.high_price:
            msg = "open_price must be within the candle range."
            raise ValueError(msg)
        if self.low_price > self.close_price or self.close_price > self.high_price:
            msg = "close_price must be within the candle range."
            raise ValueError(msg)
        if self.volume < 0:
            msg = "volume must not be negative."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class FeatureEngineeringSettings:
    history_limit: int = 120
    rolling_window: int = 20
    trend_window: int = 20
    momentum_window: int = 5
    support_window: int = 20
    resistance_window: int = 20
    liquidity_window: int = 20
    atr_window: int = 14
    tick_timeframe: Timeframe = Timeframe.ONE_MINUTE
    cache_size: int = 250

    def __post_init__(self) -> None:
        for field_name in (
            "history_limit",
            "rolling_window",
            "trend_window",
            "momentum_window",
            "support_window",
            "resistance_window",
            "liquidity_window",
            "atr_window",
            "cache_size",
        ):
            value = getattr(self, field_name)
            if value <= 0:
                msg = f"{field_name} must be greater than zero."
                raise ValueError(msg)


class FeatureEngineeringService:
    def __init__(
        self,
        *,
        repository: MarketDataRepository,
        indicator_cache: IndicatorCache,
        aggregation_engine: TimeframeAggregationEngine,
        settings: FeatureEngineeringSettings | None = None,
    ) -> None:
        self._repository = repository
        self._indicator_cache = indicator_cache
        self._aggregation_engine = aggregation_engine
        self._settings = settings or FeatureEngineeringSettings()
        self._cache: dict[tuple[str, str, str, str], deque[FeatureSet]] = defaultdict(
            lambda: deque(maxlen=self._settings.cache_size)
        )
        self._lock = asyncio.Lock()
        self._generated = 0

    @property
    def settings(self) -> FeatureEngineeringSettings:
        return self._settings

    @property
    def aggregation_engine(self) -> TimeframeAggregationEngine:
        return self._aggregation_engine

    @property
    def generated_count(self) -> int:
        return self._generated

    async def generate(self, observation: FeatureObservation) -> FeatureSet:
        candles = await self._candles_for(observation)
        current = candles[-1]
        previous = candles[-2] if len(candles) >= 2 else None

        closes = [candle.close for candle in candles]
        volumes = [candle.volume for candle in candles]
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]

        rolling_window = self._window(candles, self._settings.rolling_window)
        trend_window = self._window(candles, self._settings.trend_window)
        momentum_window = self._window(candles, self._settings.momentum_window)
        support_window = self._window(candles, self._settings.support_window)
        resistance_window = self._window(candles, self._settings.resistance_window)
        liquidity_window = self._window(candles, self._settings.liquidity_window)

        previous_close = previous.close if previous is not None else current.open
        previous_volume = previous.volume if previous is not None else current.volume

        true_range = self._true_range(current, previous_close)
        atr = await self._indicator_or_local(
            observation,
            indicator="ATR",
            period=self._settings.atr_window,
            fallback=self._average_true_range(candles[-self._settings.atr_window :]),
        )
        sma = await self._indicator_or_local(
            observation,
            indicator="SMA",
            period=self._settings.rolling_window,
            fallback=self._sma(rolling_window),
        )
        ema = await self._indicator_or_local(
            observation,
            indicator="EMA",
            period=self._settings.trend_window,
            fallback=self._ema(trend_window, self._settings.trend_window),
        )
        vwap = await self._indicator_or_local(
            observation,
            indicator="VWAP",
            period=self._settings.rolling_window,
            fallback=self._vwap(rolling_window),
        )

        session_position, time_since_open, time_until_close = await self._session_context(
            observation.exchange,
            observation.observed_at,
        )

        rolling_mean = self._mean(closes[-self._settings.rolling_window :])
        rolling_median = self._median(closes[-self._settings.rolling_window :])
        rolling_variance = self._variance(closes[-self._settings.rolling_window :])
        rolling_std_dev = math.sqrt(rolling_variance)
        rolling_volatility = rolling_std_dev / rolling_mean if rolling_mean > 0 else 0.0
        trend_change = self._trend_change(trend_window)
        momentum_change = self._momentum_change(momentum_window)
        support_distance = current.close - min(low for low in lows[-self._settings.support_window :])
        resistance_distance = max(high for high in highs[-self._settings.resistance_window :]) - current.close
        mean_volume = self._mean(volumes[-self._settings.liquidity_window :])
        relative_volume = current.volume / mean_volume if mean_volume > 0 else 0.0
        volume_ratio = current.volume / previous_volume if previous_volume > 0 else 0.0
        volume_delta = current.volume - previous_volume
        liquidity_metric = current.volume / max(true_range, 1e-9)
        liquidity_efficiency = relative_volume / max(self._high_low_range(current), 1e-9)

        gap_size = current.open - previous_close
        if observation.source_event_type == str(TICK_RECEIVED):
            gap_size = current.close - previous_close

        features = {
            "price_change": current.close - current.open,
            "log_returns": self._log_return(current.open, current.close),
            "percentage_returns": self._pct_return(current.open, current.close),
            "gap_size": gap_size,
            "gap_direction": self._direction(gap_size),
            "candle_body_size": abs(current.close - current.open),
            "upper_wick": max(0.0, current.high - max(current.open, current.close)),
            "lower_wick": max(0.0, min(current.open, current.close) - current.low),
            "true_range": true_range,
            "atr_component": true_range,
            "atr": atr,
            "rolling_mean": rolling_mean,
            "rolling_median": rolling_median,
            "rolling_variance": rolling_variance,
            "rolling_standard_deviation": rolling_std_dev,
            "rolling_volatility": rolling_volatility,
            "vwap_distance": self._relative_distance(current.close, vwap),
            "ema_distance": self._relative_distance(current.close, ema),
            "sma_distance": self._relative_distance(current.close, sma),
            "high_low_range": self._high_low_range(current),
            "open_close_range": abs(current.close - current.open),
            "volume_delta": volume_delta,
            "relative_volume": relative_volume,
            "volume_ratio": volume_ratio,
            "session_position": session_position,
            "time_since_open": time_since_open,
            "time_until_close": time_until_close,
            "momentum_window": momentum_change,
            "trend_window": trend_change,
            "support_distance": support_distance,
            "resistance_distance": resistance_distance,
            "liquidity_metric": liquidity_metric,
            "liquidity_efficiency": liquidity_efficiency,
        }

        feature_set = FeatureSet(
            feature_set_id=self._feature_set_id(observation, current),
            symbol=observation.symbol,
            exchange=observation.exchange,
            timeframe=observation.timeframe.value,
            observed_at=observation.observed_at,
            source_event_type=observation.source_event_type,
            features=features,
            metadata={
                "history_length": len(candles),
                "rolling_window": self._settings.rolling_window,
                "trend_window": self._settings.trend_window,
                "momentum_window": self._settings.momentum_window,
                "support_window": self._settings.support_window,
                "resistance_window": self._settings.resistance_window,
                "liquidity_window": self._settings.liquidity_window,
                "atr_window": self._settings.atr_window,
                "source_event_type": observation.source_event_type,
            },
            generated_at=observation.observed_at,
        )

        await self.cache(feature_set)
        return feature_set

    async def cache(self, feature_set: FeatureSet) -> None:
        key = self._cache_key(
            feature_set.symbol,
            feature_set.exchange,
            feature_set.timeframe,
            feature_set.source_event_type,
        )
        async with self._lock:
            self._cache[key].append(feature_set)
            self._generated += 1

    async def latest(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        source_event_type: str,
    ) -> FeatureSet | None:
        key = self._cache_key(symbol, exchange, timeframe, source_event_type)
        async with self._lock:
            values = self._cache.get(key)
            if not values:
                return None
            return values[-1]

    async def history(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        source_event_type: str,
    ) -> tuple[FeatureSet, ...]:
        key = self._cache_key(symbol, exchange, timeframe, source_event_type)
        async with self._lock:
            return tuple(self._cache.get(key, ()))

    async def _candles_for(self, observation: FeatureObservation) -> list[Candle]:
        timeframe = observation.timeframe
        history = list(
            await self._repository.get_candles(
                observation.symbol,
                observation.exchange,
                timeframe,
                end=observation.observed_at,
                limit=self._settings.history_limit,
            )
        )
        current = await self._current_candle(observation)
        if history and history[-1].timestamp == current.timestamp:
            history[-1] = current
        else:
            history.append(current)
        if len(history) > self._settings.history_limit:
            history = history[-self._settings.history_limit :]
        return history

    async def _current_candle(self, observation: FeatureObservation) -> Candle:
        if observation.source_event_type == str(TICK_RECEIVED):
            reference = await self._previous_price(
                observation.symbol,
                observation.exchange,
                before=observation.observed_at,
            )
            if reference is None:
                reference = observation.close_price
            high_price = max(reference, observation.close_price)
            low_price = min(reference, observation.close_price)
            open_price = reference
            close_price = observation.close_price
        else:
            open_price = observation.open_price
            high_price = observation.high_price
            low_price = observation.low_price
            close_price = observation.close_price

        return Candle(
            symbol=observation.symbol,
            exchange=observation.exchange,
            timeframe=observation.timeframe,
            timestamp=observation.observed_at,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=observation.volume,
            trades=1,
        )

    async def _previous_price(
        self, symbol: str, exchange: str, *, before: datetime
    ) -> float | None:
        ticks = await self._repository.get_ticks(symbol, exchange, end=before)
        for tick in reversed(ticks):
            if tick.timestamp < before:
                return tick.price
        candles = await self._repository.get_candles(
            symbol,
            exchange,
            self._settings.tick_timeframe,
            end=before,
        )
        for candle in reversed(candles):
            if candle.timestamp < before:
                return candle.close
        return None

    async def _indicator_or_local(
        self,
        observation: FeatureObservation,
        *,
        indicator: str,
        period: int,
        fallback: float,
    ) -> float:
        cached = await self._indicator_cache.get(
            observation.symbol,
            observation.exchange,
            observation.timeframe,
            indicator,
            period=period,
            timestamp=observation.observed_at,
        )
        if cached is None:
            return fallback
        value = cached.value
        if isinstance(value, (int, float)):
            return float(value)
        return fallback

    async def _session_context(self, exchange: str, observed_at: datetime) -> tuple[float, float, float]:
        calendar = await self._repository.get_calendar(exchange)
        if calendar is None or not calendar.sessions:
            return 0.5, 0.0, 0.0

        session = self._current_session(calendar, observed_at)
        if session is None:
            return 0.0, 0.0, 0.0

        total_seconds = (session.end - session.start).total_seconds()
        elapsed_seconds = (observed_at - session.start).total_seconds()
        remaining_seconds = (session.end - observed_at).total_seconds()
        if total_seconds <= 0:
            return 0.0, 0.0, 0.0
        return (
            max(0.0, min(1.0, elapsed_seconds / total_seconds)),
            max(0.0, elapsed_seconds),
            max(0.0, remaining_seconds),
        )

    def _current_session(self, calendar: MarketCalendar, observed_at: datetime) -> Any:
        for session in calendar.sessions:
            if session.start <= observed_at <= session.end:
                return session
        return None

    def _feature_set_id(self, observation: FeatureObservation, candle: Candle) -> str:
        return (
            f"{observation.symbol}:{observation.exchange}:{observation.timeframe.value}:"
            f"{observation.source_event_type}:{candle.timestamp.isoformat()}"
        )

    def _cache_key(self, symbol: str, exchange: str, timeframe: str, source_event_type: str) -> tuple[str, str, str, str]:
        return symbol, exchange, timeframe, source_event_type

    def _window(self, candles: list[Candle], size: int) -> list[Candle]:
        return candles[-size:] if len(candles) > size else list(candles)

    def _high_low_range(self, candle: Candle) -> float:
        return candle.high - candle.low

    def _true_range(self, current: Candle, previous_close: float) -> float:
        return max(
            current.high - current.low,
            abs(current.high - previous_close),
            abs(current.low - previous_close),
        )

    def _average_true_range(self, candles: list[Candle]) -> float:
        if not candles:
            return 0.0
        previous_close = candles[0].open
        true_ranges: list[float] = []
        for candle in candles:
            true_ranges.append(self._true_range(candle, previous_close))
            previous_close = candle.close
        return self._mean(true_ranges)

    def _trend_change(self, candles: list[Candle]) -> float:
        if len(candles) < 2:
            return 0.0
        return self._pct_return(candles[0].close, candles[-1].close)

    def _momentum_change(self, candles: list[Candle]) -> float:
        if len(candles) < 2:
            return 0.0
        return self._pct_return(candles[0].close, candles[-1].close)

    def _sma(self, candles: list[Candle]) -> float:
        return self._mean([candle.close for candle in candles])

    def _ema(self, candles: list[Candle], period: int) -> float:
        closes = [candle.close for candle in candles]
        return self._ema_values(closes, period)

    def _vwap(self, candles: list[Candle]) -> float:
        total_volume = sum(candle.volume for candle in candles)
        if total_volume <= 0:
            return candles[-1].close if candles else 0.0
        weighted = sum(((candle.high + candle.low + candle.close) / 3.0) * candle.volume for candle in candles)
        return weighted / total_volume

    def _mean(self, values: list[float]) -> float:
        return statistics.fmean(values) if values else 0.0

    def _median(self, values: list[float]) -> float:
        return statistics.median(values) if values else 0.0

    def _variance(self, values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        return statistics.pvariance(values)

    def _ema_values(self, values: list[float], period: int) -> float:
        if not values:
            return 0.0
        if period <= 1 or len(values) == 1:
            return values[-1]
        alpha = 2.0 / (period + 1.0)
        ema = values[0]
        for value in values[1:]:
            ema = (value * alpha) + (ema * (1.0 - alpha))
        return ema

    def _log_return(self, open_price: float, close_price: float) -> float:
        if open_price <= 0 or close_price <= 0:
            return 0.0
        return math.log(close_price / open_price)

    def _pct_return(self, open_price: float, close_price: float) -> float:
        if open_price <= 0:
            return 0.0
        return ((close_price - open_price) / open_price) * 100.0

    def _relative_distance(self, price: float, reference: float) -> float:
        if reference <= 0:
            return 0.0
        return ((price - reference) / reference) * 100.0

    def _direction(self, distance: float) -> float:
        if distance > 0:
            return 1.0
        if distance < 0:
            return -1.0
        return 0.0


__all__ = [
    "FeatureEngineeringService",
    "FeatureEngineeringSettings",
    "FeatureObservation",
]
