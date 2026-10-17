from __future__ import annotations

import asyncio
import math
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.feature_engine.contracts import FEATURES_UPDATED
from tranalyset.plugins.feature_engine.service import FeatureEngineeringService
from tranalyset.plugins.indicator_engine.contracts import IndicatorValue
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED, TIMEFRAME_CANDLE_CLOSED


ENGINE_VERSION = "1.0.0"
SUPPORTED_INDICATORS = (
    "EMA",
    "SMA",
    "WMA",
    "VWAP",
    "RSI",
    "MACD",
    "ATR",
    "ADX",
    "CCI",
    "ROC",
    "MFI",
    "OBV",
    "CMF",
    "Supertrend",
    "Ichimoku",
    "Donchian",
    "Keltner",
    "Bollinger",
    "Stochastic",
    "WilliamsR",
    "ParabolicSAR",
    "VolumeProfile",
)


@dataclass(frozen=True, slots=True)
class IndicatorObservation:
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
            msg = "high_price must be greater than or equal to low_price."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class IndicatorBatch:
    batch_id: str
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    source_event_type: str
    indicators: MappingProxyType[str, IndicatorValue]
    versions: MappingProxyType[str, str]
    dependencies: tuple[str, ...]
    invalidated: tuple[str, ...] = ()
    metadata: MappingProxyType[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def to_payload(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "timeframe": self.timeframe,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "source_event_type": self.source_event_type,
            "indicators": dict(self.indicators),
            "versions": dict(self.versions),
            "dependencies": self.dependencies,
            "invalidated": self.invalidated,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class _SeriesState:
    candles: deque[Candle]
    feature_set_id: str | None = None
    feature_count: int = 0
    last_timestamp: datetime | None = None
    last_candle: Candle | None = None
    last_invalidated_at: datetime | None = None
    ema_values: dict[int, float] = field(default_factory=dict)
    rsi_avg_gain: float | None = None
    rsi_avg_loss: float | None = None
    macd_signal: float | None = None
    atr_value: float | None = None
    adx_value: float | None = None
    plus_dm_ema: float | None = None
    minus_dm_ema: float | None = None
    obv_value: float = 0.0
    supertrend_upper: float | None = None
    supertrend_lower: float | None = None
    supertrend_direction: int = 1
    parabolic_sar: float | None = None
    parabolic_sar_ep: float | None = None
    parabolic_sar_af: float = 0.02
    parabolic_sar_trend: int = 1
    seeded: bool = False


@dataclass(frozen=True, slots=True)
class IndicatorEngineSettings:
    history_limit: int = 180
    ema_fast: int = 12
    ema_slow: int = 26
    ema_signal: int = 9
    sma_period: int = 20
    wma_period: int = 20
    vwap_period: int = 20
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14
    cci_period: int = 20
    roc_period: int = 12
    mfi_period: int = 14
    cmf_period: int = 20
    ichimoku_tenkan: int = 9
    ichimoku_kijun: int = 26
    ichimoku_senkou_b: int = 52
    donchian_period: int = 20
    keltner_ema_period: int = 20
    keltner_atr_period: int = 10
    bollinger_period: int = 20
    bollinger_stddev: float = 2.0
    stochastic_k_period: int = 14
    stochastic_d_period: int = 3
    williams_period: int = 14
    psar_step: float = 0.02
    psar_max_step: float = 0.2
    volume_profile_window: int = 50
    volume_profile_bin_size: float = 0.5
    tick_timeframe: Timeframe = Timeframe.ONE_MINUTE

    def __post_init__(self) -> None:
        for field_name in (
            "history_limit",
            "ema_fast",
            "ema_slow",
            "ema_signal",
            "sma_period",
            "wma_period",
            "vwap_period",
            "rsi_period",
            "atr_period",
            "adx_period",
            "cci_period",
            "roc_period",
            "mfi_period",
            "cmf_period",
            "ichimoku_tenkan",
            "ichimoku_kijun",
            "ichimoku_senkou_b",
            "donchian_period",
            "keltner_ema_period",
            "keltner_atr_period",
            "bollinger_period",
            "stochastic_k_period",
            "stochastic_d_period",
            "williams_period",
            "volume_profile_window",
        ):
            if getattr(self, field_name) <= 0:
                msg = f"{field_name} must be greater than zero."
                raise ValueError(msg)
        for field_name in ("bollinger_stddev", "psar_step", "psar_max_step", "volume_profile_bin_size"):
            if getattr(self, field_name) <= 0:
                msg = f"{field_name} must be greater than zero."
                raise ValueError(msg)


class IndicatorDependencyGraph:
    def __init__(self) -> None:
        self._dependencies: dict[str, tuple[str, ...]] = {
            "EMA": (),
            "SMA": (),
            "WMA": (),
            "VWAP": (),
            "RSI": (),
            "ATR": (),
            "ROC": (),
            "OBV": (),
            "CCI": (),
            "MFI": (),
            "CMF": (),
            "ADX": ("ATR",),
            "MACD": ("EMA",),
            "Keltner": ("EMA", "ATR"),
            "Bollinger": ("SMA",),
            "Stochastic": (),
            "WilliamsR": (),
            "Donchian": (),
            "Ichimoku": (),
            "ParabolicSAR": (),
            "Supertrend": ("ATR",),
            "VolumeProfile": (),
        }
        self._order = (
            "EMA",
            "SMA",
            "WMA",
            "VWAP",
            "ATR",
            "RSI",
            "ROC",
            "OBV",
            "CCI",
            "MFI",
            "CMF",
            "ADX",
            "MACD",
            "Keltner",
            "Bollinger",
            "Stochastic",
            "WilliamsR",
            "Donchian",
            "Ichimoku",
            "ParabolicSAR",
            "Supertrend",
            "VolumeProfile",
        )

    @property
    def order(self) -> tuple[str, ...]:
        return self._order

    def dependencies_for(self, indicator: str) -> tuple[str, ...]:
        return self._dependencies.get(indicator, ())


class IndicatorEngineService:
    def __init__(
        self,
        *,
        repository: MarketDataRepository,
        indicator_cache: IndicatorCache,
        aggregation_engine: TimeframeAggregationEngine,
        feature_engine: FeatureEngineeringService | None,
        settings: IndicatorEngineSettings | None = None,
    ) -> None:
        self._repository = repository
        self._indicator_cache = indicator_cache
        self._aggregation_engine = aggregation_engine
        self._feature_engine = feature_engine
        self._settings = settings or IndicatorEngineSettings()
        self._graph = IndicatorDependencyGraph()
        self._states: dict[tuple[str, str, str], _SeriesState] = {}
        self._latest_batches: dict[tuple[str, str, str], IndicatorBatch] = {}
        self._lock = asyncio.Lock()
        self._bootstrap_count = 0
        self._update_count = 0

    @property
    def settings(self) -> IndicatorEngineSettings:
        return self._settings

    @property
    def bootstrap_count(self) -> int:
        return self._bootstrap_count

    @property
    def update_count(self) -> int:
        return self._update_count

    async def ingest(self, observation: IndicatorObservation) -> IndicatorBatch:
        key = self._key(observation.symbol, observation.exchange, observation.timeframe)
        async with self._lock:
            state = await self._state_for(key, observation)
            invalidated: tuple[str, ...] = ()
            if state.last_timestamp is not None and observation.observed_at < state.last_timestamp:
                invalidated = await self._reset_state(key, observation, reason="out_of_order_event")
                state = await self._state_for(key, observation, force_bootstrap=False)
            candle = self._candle_from_observation(observation, state)
            self._apply_candle(state, candle)
            batch = await self._calculate_batch(key, state, observation, invalidated=invalidated)
            self._latest_batches[key] = batch
            self._update_count += 1
            return batch

    async def invalidate(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        reason: str,
        source_event_type: str,
        observed_at: datetime | None = None,
    ) -> tuple[str, ...]:
        key = self._key(symbol, exchange, timeframe)
        async with self._lock:
            return await self._reset_state(
                key,
                IndicatorObservation(
                    symbol=symbol,
                    exchange=exchange,
                    timeframe=timeframe,
                    observed_at=observed_at or datetime.now(UTC),
                    source_event_type=source_event_type,
                    open_price=1.0,
                    high_price=1.0,
                    low_price=1.0,
                    close_price=1.0,
                    volume=0.0,
                ),
                reason=reason,
            )

    async def ingest_feature_context(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        feature_set_id: str,
        feature_count: int,
        source_event_type: str,
    ) -> None:
        key = (symbol, exchange, timeframe)
        async with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            state.feature_set_id = feature_set_id
            state.feature_count = feature_count
            if state.last_timestamp is None:
                state.last_timestamp = datetime.now(UTC)
            self._latest_batches.pop(key, None)
            # Feature context is supplemental, so we do not recompute here.

    async def latest(self, *, symbol: str, exchange: str, timeframe: str) -> IndicatorBatch | None:
        return self._latest_batches.get(self._key(symbol, exchange, Timeframe(timeframe)))

    async def _state_for(
        self,
        key: tuple[str, str, str],
        observation: IndicatorObservation,
        *,
        force_bootstrap: bool = True,
    ) -> _SeriesState:
        state = self._states.get(key)
        if state is None:
            state = _SeriesState(candles=deque(maxlen=self._settings.history_limit))
            self._states[key] = state
        if force_bootstrap and not state.seeded:
            await self._seed_history(key, observation, state)
        return state

    async def _seed_history(
        self,
        key: tuple[str, str, str],
        observation: IndicatorObservation,
        state: _SeriesState,
    ) -> None:
        candles = await self._repository.get_candles(
            observation.symbol,
            observation.exchange,
            observation.timeframe,
            limit=self._settings.history_limit,
        )
        for candle in candles:
            self._apply_candle(state, candle)
        state.seeded = True
        if candles:
            self._bootstrap_count += 1
            state.last_timestamp = candles[-1].timestamp
            state.last_candle = candles[-1]

    def _candle_from_observation(
        self,
        observation: IndicatorObservation,
        state: _SeriesState,
    ) -> Candle:
        if observation.source_event_type == str(TICK_RECEIVED):
            previous_close = state.last_candle.close if state.last_candle is not None else observation.close_price
            open_price = previous_close
            high_price = max(previous_close, observation.close_price)
            low_price = min(previous_close, observation.close_price)
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

    def _apply_candle(self, state: _SeriesState, candle: Candle) -> None:
        if state.candles and state.candles[-1].timestamp == candle.timestamp:
            state.candles[-1] = candle
        else:
            state.candles.append(candle)
        state.last_timestamp = candle.timestamp
        state.last_candle = candle

    async def _calculate_batch(
        self,
        key: tuple[str, str, str],
        state: _SeriesState,
        observation: IndicatorObservation,
        *,
        invalidated: tuple[str, ...],
    ) -> IndicatorBatch:
        indicators: dict[str, IndicatorValue] = {}
        versions: dict[str, str] = {}
        dependencies = self._graph.dependencies_for
        current = state.last_candle
        previous = state.candles[-2] if len(state.candles) >= 2 else None
        if current is None:
            msg = "Indicator state is missing the current candle."
            raise RuntimeError(msg)

        feature_set = await self._feature_context(observation, state)
        metadata = {
            "feature_set_id": feature_set.get("feature_set_id"),
            "feature_count": feature_set.get("feature_count", 0),
            "history_length": len(state.candles),
            "dependency_graph_version": "1",
            "engine_version": ENGINE_VERSION,
        }

        calculators = {
            "EMA": lambda: self._ema(state, current.close, self._settings.ema_slow),
            "SMA": lambda: self._sma(state, self._settings.sma_period),
            "WMA": lambda: self._wma(state, self._settings.wma_period),
            "VWAP": lambda: self._vwap(state, self._settings.vwap_period),
            "RSI": lambda: self._rsi(state, current.close),
            "MACD": lambda: self._macd(state, current.close),
            "ATR": lambda: self._atr(state, current),
            "ADX": lambda: self._adx(state, current, previous),
            "CCI": lambda: self._cci(state, self._settings.cci_period),
            "ROC": lambda: self._roc(state, self._settings.roc_period),
            "MFI": lambda: self._mfi(state, self._settings.mfi_period),
            "OBV": lambda: self._obv(state, current, previous),
            "CMF": lambda: self._cmf(state, self._settings.cmf_period),
            "Supertrend": lambda: self._supertrend(state, current),
            "Ichimoku": lambda: self._ichimoku(state),
            "Donchian": lambda: self._donchian(state, self._settings.donchian_period),
            "Keltner": lambda: self._keltner(state, current),
            "Bollinger": lambda: self._bollinger(state, self._settings.bollinger_period),
            "Stochastic": lambda: self._stochastic(state, self._settings.stochastic_k_period),
            "WilliamsR": lambda: self._williams_r(state, self._settings.williams_period),
            "ParabolicSAR": lambda: self._parabolic_sar(state),
            "VolumeProfile": lambda: self._volume_profile(state),
        }

        for indicator in self._graph.order:
            value = calculators[indicator]()
            indicators[indicator] = value
            versions[indicator] = self._version_for(indicator)
            await self._indicator_cache.put(
                observation.symbol,
                observation.exchange,
                observation.timeframe,
                indicator,
                current.timestamp,
                value,
                period=self._period_for(indicator),
                version=versions[indicator],
                metadata={
                    "feature_set_id": feature_set.get("feature_set_id"),
                    "dependencies": dependencies(indicator),
                    "engine_version": ENGINE_VERSION,
                },
            )

        batch = IndicatorBatch(
            batch_id=self._batch_id(observation),
            symbol=observation.symbol,
            exchange=observation.exchange,
            timeframe=observation.timeframe.value,
            observed_at=current.timestamp,
            source_event_type=observation.source_event_type,
            indicators=MappingProxyType(indicators),
            versions=MappingProxyType(versions),
            dependencies=tuple(self._graph.order),
            invalidated=invalidated,
            metadata=MappingProxyType(metadata),
        )
        return batch

    async def _feature_context(
        self,
        observation: IndicatorObservation,
        state: _SeriesState,
    ) -> dict[str, Any]:
        if self._feature_engine is None:
            return {
                "feature_set_id": state.feature_set_id,
                "feature_count": state.feature_count,
            }
        latest = await self._feature_engine.latest(
            symbol=observation.symbol,
            exchange=observation.exchange,
            timeframe=observation.timeframe.value,
            source_event_type=observation.source_event_type,
        )
        if latest is None:
            return {
                "feature_set_id": state.feature_set_id,
                "feature_count": state.feature_count,
            }
        state.feature_set_id = latest.feature_set_id
        state.feature_count = len(latest.features)
        return {
            "feature_set_id": latest.feature_set_id,
            "feature_count": len(latest.features),
        }

    async def _reset_state(
        self,
        key: tuple[str, str, str],
        observation: IndicatorObservation,
        *,
        reason: str,
    ) -> tuple[str, ...]:
        state = self._states.get(key)
        invalidated: list[str] = []
        if state is not None:
            invalidated = list(self._graph.order)
            state.candles.clear()
            state.ema_values.clear()
            state.rsi_avg_gain = None
            state.rsi_avg_loss = None
            state.macd_signal = None
            state.atr_value = None
            state.adx_value = None
            state.plus_dm_ema = None
            state.minus_dm_ema = None
            state.obv_value = 0.0
            state.supertrend_upper = None
            state.supertrend_lower = None
            state.supertrend_direction = 1
            state.parabolic_sar = None
            state.parabolic_sar_ep = None
            state.parabolic_sar_af = 0.02
            state.parabolic_sar_trend = 1
            state.seeded = False
            state.last_timestamp = None
            state.last_candle = None
        await self._indicator_cache.invalidate(
            observation.symbol,
            observation.exchange,
            observation.timeframe,
            "*",
        )
        return tuple(invalidated)

    def _batch_id(self, observation: IndicatorObservation) -> str:
        return (
            f"{observation.symbol}:{observation.exchange}:{observation.timeframe.value}:"
            f"{observation.source_event_type}:{observation.observed_at.isoformat()}"
        )

    def _version_for(self, indicator: str) -> str:
        return {
            "EMA": f"{ENGINE_VERSION}:ema:{self._settings.ema_slow}",
            "SMA": f"{ENGINE_VERSION}:sma:{self._settings.sma_period}",
            "WMA": f"{ENGINE_VERSION}:wma:{self._settings.wma_period}",
            "VWAP": f"{ENGINE_VERSION}:vwap:{self._settings.vwap_period}",
            "RSI": f"{ENGINE_VERSION}:rsi:{self._settings.rsi_period}",
            "MACD": f"{ENGINE_VERSION}:macd:{self._settings.ema_fast}:{self._settings.ema_slow}:{self._settings.ema_signal}",
            "ATR": f"{ENGINE_VERSION}:atr:{self._settings.atr_period}",
            "ADX": f"{ENGINE_VERSION}:adx:{self._settings.adx_period}",
            "CCI": f"{ENGINE_VERSION}:cci:{self._settings.cci_period}",
            "ROC": f"{ENGINE_VERSION}:roc:{self._settings.roc_period}",
            "MFI": f"{ENGINE_VERSION}:mfi:{self._settings.mfi_period}",
            "OBV": f"{ENGINE_VERSION}:obv",
            "CMF": f"{ENGINE_VERSION}:cmf:{self._settings.cmf_period}",
            "Supertrend": f"{ENGINE_VERSION}:supertrend:{self._settings.atr_period}",
            "Ichimoku": f"{ENGINE_VERSION}:ichimoku:{self._settings.ichimoku_tenkan}:{self._settings.ichimoku_kijun}:{self._settings.ichimoku_senkou_b}",
            "Donchian": f"{ENGINE_VERSION}:donchian:{self._settings.donchian_period}",
            "Keltner": f"{ENGINE_VERSION}:keltner:{self._settings.keltner_ema_period}:{self._settings.keltner_atr_period}",
            "Bollinger": f"{ENGINE_VERSION}:bollinger:{self._settings.bollinger_period}:{self._settings.bollinger_stddev}",
            "Stochastic": f"{ENGINE_VERSION}:stochastic:{self._settings.stochastic_k_period}:{self._settings.stochastic_d_period}",
            "WilliamsR": f"{ENGINE_VERSION}:williams:{self._settings.williams_period}",
            "ParabolicSAR": f"{ENGINE_VERSION}:psar:{self._settings.psar_step}:{self._settings.psar_max_step}",
            "VolumeProfile": f"{ENGINE_VERSION}:volume_profile:{self._settings.volume_profile_window}:{self._settings.volume_profile_bin_size}",
        }[indicator]

    def _period_for(self, indicator: str) -> int | None:
        return {
            "EMA": self._settings.ema_slow,
            "SMA": self._settings.sma_period,
            "WMA": self._settings.wma_period,
            "VWAP": self._settings.vwap_period,
            "RSI": self._settings.rsi_period,
            "MACD": self._settings.ema_slow,
            "ATR": self._settings.atr_period,
            "ADX": self._settings.adx_period,
            "CCI": self._settings.cci_period,
            "ROC": self._settings.roc_period,
            "MFI": self._settings.mfi_period,
            "OBV": None,
            "CMF": self._settings.cmf_period,
            "Supertrend": self._settings.atr_period,
            "Ichimoku": None,
            "Donchian": self._settings.donchian_period,
            "Keltner": self._settings.keltner_ema_period,
            "Bollinger": self._settings.bollinger_period,
            "Stochastic": self._settings.stochastic_k_period,
            "WilliamsR": self._settings.williams_period,
            "ParabolicSAR": None,
            "VolumeProfile": self._settings.volume_profile_window,
        }.get(indicator)

    def _close_window(self, state: _SeriesState, period: int) -> list[Candle]:
        return list(state.candles)[-period:]

    def _ema(self, state: _SeriesState, close: float, period: int) -> float:
        previous = state.ema_values.get(period)
        if previous is None:
            window = [candle.close for candle in self._close_window(state, period)]
            previous = statistics.fmean(window) if window else close
        alpha = 2.0 / (period + 1.0)
        ema = previous + alpha * (close - previous)
        state.ema_values[period] = ema
        return ema

    def _sma(self, state: _SeriesState, period: int) -> float:
        closes = [candle.close for candle in self._close_window(state, period)]
        return statistics.fmean(closes) if closes else 0.0

    def _wma(self, state: _SeriesState, period: int) -> float:
        closes = [candle.close for candle in self._close_window(state, period)]
        if not closes:
            return 0.0
        weights = range(1, len(closes) + 1)
        numerator = sum(value * weight for value, weight in zip(closes, weights, strict=False))
        denominator = sum(weights)
        return numerator / denominator if denominator else 0.0

    def _vwap(self, state: _SeriesState, period: int) -> float:
        candles = self._close_window(state, period)
        total_volume = sum(candle.volume for candle in candles)
        if total_volume <= 0:
            return candles[-1].close if candles else 0.0
        weighted = sum(((candle.high + candle.low + candle.close) / 3.0) * candle.volume for candle in candles)
        return weighted / total_volume

    def _rsi(self, state: _SeriesState, close: float) -> float:
        if len(state.candles) < 2:
            return 50.0
        previous_close = state.candles[-2].close
        change = close - previous_close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        period = self._settings.rsi_period
        if state.rsi_avg_gain is None or state.rsi_avg_loss is None:
            gains = [max(state.candles[i].close - state.candles[i - 1].close, 0.0) for i in range(1, min(len(state.candles), period + 1))]
            losses = [max(state.candles[i - 1].close - state.candles[i].close, 0.0) for i in range(1, min(len(state.candles), period + 1))]
            state.rsi_avg_gain = statistics.fmean(gains) if gains else gain
            state.rsi_avg_loss = statistics.fmean(losses) if losses else loss
        else:
            state.rsi_avg_gain = ((state.rsi_avg_gain * (period - 1)) + gain) / period
            state.rsi_avg_loss = ((state.rsi_avg_loss * (period - 1)) + loss) / period
        if state.rsi_avg_loss == 0:
            return 100.0
        rs = state.rsi_avg_gain / state.rsi_avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _macd(self, state: _SeriesState, close: float) -> tuple[float, float, float]:
        fast = self._ema(state, close, self._settings.ema_fast)
        slow = self._ema(state, close, self._settings.ema_slow)
        macd_line = fast - slow
        if state.macd_signal is None:
            state.macd_signal = macd_line
        else:
            alpha = 2.0 / (self._settings.ema_signal + 1.0)
            state.macd_signal = state.macd_signal + alpha * (macd_line - state.macd_signal)
        histogram = macd_line - state.macd_signal
        return (macd_line, state.macd_signal, histogram)

    def _true_range(self, current: Candle, previous: Candle | None) -> float:
        if previous is None:
            return current.high - current.low
        return max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )

    def _atr(self, state: _SeriesState, current: Candle) -> float:
        previous = state.candles[-2] if len(state.candles) >= 2 else None
        true_range = self._true_range(current, previous)
        period = self._settings.atr_period
        if state.atr_value is None:
            ranges = [self._true_range(state.candles[i], state.candles[i - 1] if i > 0 else None) for i in range(min(len(state.candles), period))]
            state.atr_value = statistics.fmean(ranges) if ranges else true_range
        else:
            state.atr_value = ((state.atr_value * (period - 1)) + true_range) / period
        return state.atr_value

    def _adx(self, state: _SeriesState, current: Candle, previous: Candle | None) -> float:
        if previous is None:
            return 0.0
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
        atr = self._atr(state, current)
        period = self._settings.adx_period
        if state.plus_dm_ema is None:
            state.plus_dm_ema = plus_dm
            state.minus_dm_ema = minus_dm
            state.adx_value = 0.0
        else:
            state.plus_dm_ema = ((state.plus_dm_ema * (period - 1)) + plus_dm) / period
            state.minus_dm_ema = ((state.minus_dm_ema * (period - 1)) + minus_dm) / period
        if atr <= 0:
            return 0.0
        plus_di = 100.0 * (state.plus_dm_ema / atr)
        minus_di = 100.0 * (state.minus_dm_ema / atr)
        denominator = plus_di + minus_di
        dx = 0.0 if denominator == 0 else 100.0 * abs(plus_di - minus_di) / denominator
        if state.adx_value == 0.0:
            state.adx_value = dx
        else:
            state.adx_value = ((state.adx_value * (period - 1)) + dx) / period
        return state.adx_value

    def _cci(self, state: _SeriesState, period: int) -> float:
        candles = self._close_window(state, period)
        if not candles:
            return 0.0
        typical_prices = [(candle.high + candle.low + candle.close) / 3.0 for candle in candles]
        sma = statistics.fmean(typical_prices)
        mean_deviation = statistics.fmean([abs(price - sma) for price in typical_prices])
        if mean_deviation == 0:
            return 0.0
        current_typical = typical_prices[-1]
        return (current_typical - sma) / (0.015 * mean_deviation)

    def _roc(self, state: _SeriesState, period: int) -> float:
        if len(state.candles) <= period:
            return 0.0
        past_close = state.candles[-period - 1].close
        current_close = state.candles[-1].close
        if past_close == 0:
            return 0.0
        return ((current_close - past_close) / past_close) * 100.0

    def _mfi(self, state: _SeriesState, period: int) -> float:
        candles = self._close_window(state, period + 1)
        if len(candles) < 2:
            return 50.0
        positive_flow = 0.0
        negative_flow = 0.0
        for previous, current in zip(candles, candles[1:], strict=False):
            prev_typical = (previous.high + previous.low + previous.close) / 3.0
            current_typical = (current.high + current.low + current.close) / 3.0
            money_flow = current_typical * current.volume
            if current_typical > prev_typical:
                positive_flow += money_flow
            elif current_typical < prev_typical:
                negative_flow += money_flow
        if negative_flow == 0:
            return 100.0
        money_ratio = positive_flow / negative_flow
        return 100.0 - (100.0 / (1.0 + money_ratio))

    def _obv(self, state: _SeriesState, current: Candle, previous: Candle | None) -> float:
        if previous is None:
            return state.obv_value
        if current.close > previous.close:
            state.obv_value += current.volume
        elif current.close < previous.close:
            state.obv_value -= current.volume
        return state.obv_value

    def _cmf(self, state: _SeriesState, period: int) -> float:
        candles = self._close_window(state, period)
        if not candles:
            return 0.0
        mfv_sum = 0.0
        volume_sum = 0.0
        for candle in candles:
            range_size = candle.high - candle.low
            money_flow_multiplier = 0.0 if range_size == 0 else ((candle.close - candle.low) - (candle.high - candle.close)) / range_size
            mfv_sum += money_flow_multiplier * candle.volume
            volume_sum += candle.volume
        return 0.0 if volume_sum == 0 else mfv_sum / volume_sum

    def _supertrend(self, state: _SeriesState, current: Candle) -> tuple[float, int]:
        atr = state.atr_value or self._atr(state, current)
        hl2 = (current.high + current.low) / 2.0
        multiplier = 3.0
        upperband = hl2 + (multiplier * atr)
        lowerband = hl2 - (multiplier * atr)
        if state.supertrend_upper is None or state.supertrend_lower is None:
            state.supertrend_upper = upperband
            state.supertrend_lower = lowerband
            state.supertrend_direction = 1
            return (lowerband, state.supertrend_direction)
        state.supertrend_upper = upperband if upperband < state.supertrend_upper or current.close > state.supertrend_upper else state.supertrend_upper
        state.supertrend_lower = lowerband if lowerband > state.supertrend_lower or current.close < state.supertrend_lower else state.supertrend_lower
        if state.supertrend_direction == -1 and current.close > state.supertrend_upper:
            state.supertrend_direction = 1
        elif state.supertrend_direction == 1 and current.close < state.supertrend_lower:
            state.supertrend_direction = -1
        supertrend = state.supertrend_lower if state.supertrend_direction == 1 else state.supertrend_upper
        return (supertrend, state.supertrend_direction)

    def _ichimoku(self, state: _SeriesState) -> tuple[float, float, float, float, float]:
        tenkan_window = self._close_window(state, self._settings.ichimoku_tenkan)
        kijun_window = self._close_window(state, self._settings.ichimoku_kijun)
        senkou_window = self._close_window(state, self._settings.ichimoku_senkou_b)
        if not tenkan_window or not kijun_window or not senkou_window:
            close = state.candles[-1].close if state.candles else 0.0
            return (close, close, close, close, close)
        tenkan = (max(c.high for c in tenkan_window) + min(c.low for c in tenkan_window)) / 2.0
        kijun = (max(c.high for c in kijun_window) + min(c.low for c in kijun_window)) / 2.0
        senkou_a = (tenkan + kijun) / 2.0
        senkou_b = (max(c.high for c in senkou_window) + min(c.low for c in senkou_window)) / 2.0
        chikou = state.candles[-1].close if state.candles else 0.0
        return (tenkan, kijun, senkou_a, senkou_b, chikou)

    def _donchian(self, state: _SeriesState, period: int) -> tuple[float, float, float]:
        candles = self._close_window(state, period)
        if not candles:
            return (0.0, 0.0, 0.0)
        high = max(candle.high for candle in candles)
        low = min(candle.low for candle in candles)
        return (high, low, (high + low) / 2.0)

    def _keltner(self, state: _SeriesState, current: Candle) -> tuple[float, float, float]:
        middle = self._ema(state, current.close, self._settings.keltner_ema_period)
        atr = self._atr(state, current)
        multiplier = 2.0
        upper = middle + (multiplier * atr)
        lower = middle - (multiplier * atr)
        return (middle, upper, lower)

    def _bollinger(self, state: _SeriesState, period: int) -> tuple[float, float, float]:
        closes = [candle.close for candle in self._close_window(state, period)]
        if not closes:
            return (0.0, 0.0, 0.0)
        middle = statistics.fmean(closes)
        std_dev = statistics.pstdev(closes) if len(closes) > 1 else 0.0
        upper = middle + (self._settings.bollinger_stddev * std_dev)
        lower = middle - (self._settings.bollinger_stddev * std_dev)
        return (middle, upper, lower)

    def _stochastic(self, state: _SeriesState, period: int) -> tuple[float, float]:
        candles = self._close_window(state, period)
        if not candles:
            return (0.0, 0.0)
        high = max(candle.high for candle in candles)
        low = min(candle.low for candle in candles)
        close = candles[-1].close
        k = 0.0 if high == low else ((close - low) / (high - low)) * 100.0
        recent_k = [self._stochastic_k_value(candles[: index + 1], period) for index in range(len(candles))]
        d_window = recent_k[-self._settings.stochastic_d_period :]
        d = statistics.fmean(d_window) if d_window else k
        return (k, d)

    def _stochastic_k_value(self, candles: list[Candle], period: int) -> float:
        window = candles[-period:]
        high = max(candle.high for candle in window)
        low = min(candle.low for candle in window)
        close = window[-1].close
        return 0.0 if high == low else ((close - low) / (high - low)) * 100.0

    def _williams_r(self, state: _SeriesState, period: int) -> float:
        candles = self._close_window(state, period)
        if not candles:
            return 0.0
        high = max(candle.high for candle in candles)
        low = min(candle.low for candle in candles)
        close = candles[-1].close
        if high == low:
            return 0.0
        return ((high - close) / (high - low)) * -100.0

    def _parabolic_sar(self, state: _SeriesState) -> tuple[float, int]:
        candles = list(state.candles)
        if len(candles) < 2:
            close = candles[-1].close if candles else 0.0
            state.parabolic_sar = close
            return (close, state.parabolic_sar_trend)
        current = candles[-1]
        previous = candles[-2]
        if state.parabolic_sar is None:
            state.parabolic_sar = previous.low
            state.parabolic_sar_ep = current.high
            state.parabolic_sar_trend = 1
            state.parabolic_sar_af = self._settings.psar_step
        sar = state.parabolic_sar + state.parabolic_sar_af * ((state.parabolic_sar_ep or current.high) - state.parabolic_sar)
        if state.parabolic_sar_trend == 1:
            if current.low < sar:
                state.parabolic_sar_trend = -1
                sar = state.parabolic_sar_ep or current.high
                state.parabolic_sar_ep = current.low
                state.parabolic_sar_af = self._settings.psar_step
            else:
                if current.high > (state.parabolic_sar_ep or current.high):
                    state.parabolic_sar_ep = current.high
                    state.parabolic_sar_af = min(state.parabolic_sar_af + self._settings.psar_step, self._settings.psar_max_step)
        else:
            if current.high > sar:
                state.parabolic_sar_trend = 1
                sar = state.parabolic_sar_ep or current.low
                state.parabolic_sar_ep = current.high
                state.parabolic_sar_af = self._settings.psar_step
            else:
                if current.low < (state.parabolic_sar_ep or current.low):
                    state.parabolic_sar_ep = current.low
                    state.parabolic_sar_af = min(state.parabolic_sar_af + self._settings.psar_step, self._settings.psar_max_step)
        state.parabolic_sar = sar
        return (sar, state.parabolic_sar_trend)

    def _volume_profile(self, state: _SeriesState) -> tuple[float, float, float]:
        candles = self._close_window(state, self._settings.volume_profile_window)
        if not candles:
            return (0.0, 0.0, 0.0)
        bin_size = self._settings.volume_profile_bin_size
        profile: dict[float, float] = defaultdict(float)
        for candle in candles:
            price = (candle.high + candle.low + candle.close) / 3.0
            bucket = round(price / bin_size) * bin_size
            profile[bucket] += candle.volume
        if not profile:
            return (0.0, 0.0, 0.0)
        poc = max(profile.items(), key=lambda item: item[1])[0]
        total_volume = sum(profile.values())
        sorted_bins = sorted(profile.items(), key=lambda item: item[0])
        cumulative = 0.0
        val = sorted_bins[0][0]
        vah = sorted_bins[-1][0]
        for price, volume in sorted_bins:
            cumulative += volume
            if cumulative <= total_volume * 0.15:
                val = price
            if cumulative >= total_volume * 0.85:
                vah = price
                break
        return (poc, vah, val)

    async def _latest_feature_context(self, observation: IndicatorObservation) -> dict[str, Any]:
        if self._feature_engine is None:
            return {}
        latest = await self._feature_engine.latest(
            symbol=observation.symbol,
            exchange=observation.exchange,
            timeframe=observation.timeframe.value,
            source_event_type=observation.source_event_type,
        )
        if latest is None:
            return {}
        return {
            "feature_set_id": latest.feature_set_id,
            "feature_count": len(latest.features),
        }

    def _key(self, symbol: str, exchange: str, timeframe: Timeframe) -> tuple[str, str, str]:
        return (symbol, exchange, timeframe.value)


__all__ = [
    "ENGINE_VERSION",
    "IndicatorBatch",
    "IndicatorDependencyGraph",
    "IndicatorEngineService",
    "IndicatorEngineSettings",
    "IndicatorObservation",
    "SUPPORTED_INDICATORS",
]
