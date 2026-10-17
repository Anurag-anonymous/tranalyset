from __future__ import annotations

import asyncio
import math
import statistics
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Any

from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.feature_engine.contracts import FeatureSet
from tranalyset.plugins.indicator_engine.contracts import IndicatorsUpdatedPayload


@dataclass(frozen=True, slots=True)
class PatternObservation:
    symbol: str
    exchange: str
    timeframe: Timeframe
    observed_at: datetime
    source_event_type: str
    candle: Candle
    indicators: Mapping[str, Any] = field(default_factory=dict)
    features: Mapping[str, Any] = field(default_factory=dict)
    market_regime: str | None = None
    market_regime_confidence: float = 0.0

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


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    pattern_id: str
    pattern: str
    category: str
    confidence: float
    strength: float
    age_bars: int
    validity: bool
    historical_success_rate: float
    reliability: float
    current_context: Mapping[str, float | str | bool]
    volume_confirmation: float
    trend_confirmation: float
    volatility: float
    market_regime: str | None
    evidence: Mapping[str, float | str | bool]
    source_event_type: str
    previous_confidence: float | None = None
    previous_strength: float | None = None
    reason: str | None = None


@dataclass(slots=True)
class _PatternState:
    pattern: str
    category: str
    first_seen_at: datetime
    last_seen_at: datetime
    age_bars: int = 1
    confidence: float = 0.0
    strength: float = 0.0
    valid: bool = True
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class PatternIntelligenceSettings:
    history_limit: int = 120
    lookback_bars: int = 30
    min_confidence: float = 0.55
    min_strength: float = 55.0
    update_delta: float = 0.08
    confirmation_age: int = 2
    trend_confirm_threshold: float = 0.55
    volume_confirm_threshold: float = 0.55
    volatility_threshold: float = 0.4
    regime_threshold: float = 0.5
    tick_timeframe: Timeframe = Timeframe.ONE_MINUTE
    max_tracked_series: int = 5_000

    def __post_init__(self) -> None:
        for field_name in ("history_limit", "lookback_bars", "confirmation_age", "max_tracked_series"):
            if getattr(self, field_name) <= 0:
                msg = f"{field_name} must be greater than zero."
                raise ValueError(msg)
        for field_name in ("min_confidence", "update_delta", "trend_confirm_threshold", "volume_confirm_threshold", "volatility_threshold", "regime_threshold"):
            if getattr(self, field_name) < 0:
                msg = f"{field_name} must not be negative."
                raise ValueError(msg)


class PatternIntelligenceService:
    def __init__(
        self,
        *,
        repository: MarketDataRepository,
        indicator_cache: IndicatorCache,
        aggregation_engine: TimeframeAggregationEngine,
        settings: PatternIntelligenceSettings | None = None,
    ) -> None:
        self._repository = repository
        self._indicator_cache = indicator_cache
        self._aggregation_engine = aggregation_engine
        self._settings = settings or PatternIntelligenceSettings()
        self._lock = asyncio.Lock()
        self._tracked_series: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._candles_seen: dict[tuple[str, str, str], datetime] = {}
        self._indicator_context: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        self._feature_context: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(dict)
        self._regime_context: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        self._active_patterns: dict[tuple[str, str, str, str], _PatternState] = {}
        self._stats: dict[str, dict[str, int]] = defaultdict(lambda: {"detections": 0, "confirmed": 0, "invalidated": 0})
        self._event_counts = {
            "candle": 0,
            "indicator": 0,
            "feature": 0,
            "regime": 0,
        }
        self._generated = 0

    @property
    def generated(self) -> int:
        return self._generated

    async def update_indicator_context(self, payload: IndicatorsUpdatedPayload) -> None:
        key = (payload.symbol, payload.exchange, payload.timeframe)
        self._touch_series(key)
        self._indicator_context[key] = {
            "indicators": dict(payload.indicators),
            "versions": dict(payload.versions),
            "observed_at": payload.observed_at,
            "source_event_type": payload.source_event_type,
        }

    async def update_feature_context(self, feature_set: FeatureSet) -> None:
        key = (feature_set.symbol, feature_set.exchange, feature_set.timeframe)
        self._touch_series(key)
        self._feature_context[key] = {
            "feature_set_id": feature_set.feature_set_id,
            "features": dict(feature_set.features),
            "metadata": dict(feature_set.metadata),
            "observed_at": feature_set.observed_at,
            "source_event_type": feature_set.source_event_type,
        }

    async def update_market_regime(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        regime: str,
        confidence: float,
        observed_at: datetime,
        source_event_type: str,
    ) -> None:
        key = (symbol, exchange, timeframe.value)
        self._touch_series(key)
        self._regime_context[key] = {
            "regime": regime,
            "confidence": confidence,
            "observed_at": observed_at,
            "source_event_type": source_event_type,
        }

    async def ingest_candle(self, candle: Candle, *, source_event_type: str) -> tuple[PatternCandidate, ...]:
        key = (candle.symbol, candle.exchange, candle.timeframe.value)
        async with self._lock:
            self._touch_series(key)
            if self._candles_seen.get(key) == candle.timestamp:
                return ()
            self._candles_seen[key] = candle.timestamp
            await self._repository.append_candle(candle)
            observation = await self._observation_for(candle, source_event_type)
            candidates = await self._evaluate(observation)
            return await self._apply(candidates, observation)

    async def ingest_snapshot(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        observed_at: datetime,
        source_event_type: str,
    ) -> tuple[PatternCandidate, ...]:
        candle = await self._repository.get_latest_candle(symbol, exchange, timeframe)
        if candle is None:
            return ()
        observation = await self._observation_for(candle, source_event_type)
        async with self._lock:
            self._touch_series((symbol, exchange, timeframe.value))
            candidates = await self._evaluate(observation)
            return await self._apply(candidates, observation)

    async def invalidate(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        reason: str,
        source_event_type: str,
    ) -> tuple[PatternCandidate, ...]:
        key_prefix = (symbol, exchange, timeframe.value)
        async with self._lock:
            invalidated: list[PatternCandidate] = []
            for key in list(self._active_patterns):
                if key[:3] != key_prefix:
                    continue
                state = self._active_patterns.pop(key)
                self._stats[state.pattern]["invalidated"] += 1
                invalidated.append(
                    PatternCandidate(
                        pattern_id=key[3],
                        pattern=state.pattern,
                        category=state.category,
                        confidence=state.confidence,
                        strength=state.strength,
                        age_bars=state.age_bars,
                        validity=False,
                        historical_success_rate=self._success_rate(state.pattern),
                        reliability=self._reliability(state.pattern, 0.0, 0.0),
                        current_context={},
                        volume_confirmation=0.0,
                        trend_confirmation=0.0,
                        volatility=0.0,
                        market_regime=None,
                        evidence={"reason": reason},
                        source_event_type=source_event_type,
                        previous_confidence=state.confidence,
                        previous_strength=state.strength,
                        reason=reason,
                    )
                )
            self._generated += len(invalidated)
            return tuple(invalidated)

    async def health_snapshot(self) -> dict[str, int]:
        return {
            **self._event_counts,
            "tracked_series": len(self._tracked_series),
            "active_patterns": len(self._active_patterns),
        }

    def _touch_series(self, key: tuple[str, str, str]) -> None:
        if key in self._tracked_series:
            self._tracked_series.move_to_end(key)
            return
        self._tracked_series[key] = None
        while len(self._tracked_series) > self._settings.max_tracked_series:
            expired, _ = self._tracked_series.popitem(last=False)
            self._candles_seen.pop(expired, None)
            self._indicator_context.pop(expired, None)
            self._feature_context.pop(expired, None)
            self._regime_context.pop(expired, None)
            for pattern_key in tuple(self._active_patterns):
                if pattern_key[:3] == expired:
                    self._active_patterns.pop(pattern_key, None)

    async def _observation_for(self, candle: Candle, source_event_type: str) -> PatternObservation:
        key = (candle.symbol, candle.exchange, candle.timeframe.value)
        indicators = self._indicator_context.get(key, {}).get("indicators", {})
        feature_context = self._latest_feature_context(candle.symbol, candle.exchange, candle.timeframe.value)
        features = feature_context.get("features", {})
        regime_context = self._regime_context.get(key, {})
        regime = regime_context.get("regime")
        regime_confidence = float(regime_context.get("confidence", 0.0) or 0.0)
        return PatternObservation(
            symbol=candle.symbol,
            exchange=candle.exchange,
            timeframe=candle.timeframe,
            observed_at=candle.timestamp,
            source_event_type=source_event_type,
            candle=candle,
            indicators=indicators,
            features=features,
            market_regime=regime if isinstance(regime, str) else None,
            market_regime_confidence=regime_confidence,
        )

    async def _evaluate(self, observation: PatternObservation) -> tuple[PatternCandidate, ...]:
        candles = await self._repository.get_candles(
            observation.symbol,
            observation.exchange,
            observation.timeframe,
            limit=self._settings.lookback_bars,
        )
        candidates = list(self._detect_patterns(observation, candles))
        candidates.sort(key=lambda candidate: candidate.strength, reverse=True)
        return tuple(candidates)

    async def _apply(
        self,
        candidates: tuple[PatternCandidate, ...],
        observation: PatternObservation,
    ) -> tuple[PatternCandidate, ...]:
        published: list[PatternCandidate] = []
        seen_keys: set[tuple[str, str, str, str]] = set()
        for candidate in candidates:
            key = (observation.symbol, observation.exchange, observation.timeframe.value, candidate.pattern_id)
            seen_keys.add(key)
            previous = self._active_patterns.get(key)
            if previous is None:
                self._active_patterns[key] = _PatternState(
                    pattern=candidate.pattern,
                    category=candidate.category,
                    first_seen_at=observation.observed_at,
                    last_seen_at=observation.observed_at,
                    age_bars=candidate.age_bars,
                    confidence=candidate.confidence,
                    strength=candidate.strength,
                    valid=candidate.validity,
                )
                self._stats[candidate.pattern]["detections"] += 1
                if candidate.validity:
                    published.append(candidate)
                continue

            previous.last_seen_at = observation.observed_at
            previous.age_bars = candidate.age_bars
            previous.valid = candidate.validity
            if candidate.validity and not previous.confirmed and candidate.age_bars >= self._settings.confirmation_age:
                previous.confirmed = True
                self._stats[candidate.pattern]["confirmed"] += 1

            if not candidate.validity:
                self._active_patterns.pop(key, None)
                self._stats[candidate.pattern]["invalidated"] += 1
                published.append(candidate)
                continue

            confidence_delta = abs(candidate.confidence - previous.confidence)
            strength_delta = abs(candidate.strength - previous.strength)
            if confidence_delta >= self._settings.update_delta or strength_delta >= (self._settings.update_delta * 100.0):
                published.append(
                    PatternCandidate(
                        pattern_id=candidate.pattern_id,
                        pattern=candidate.pattern,
                        category=candidate.category,
                        confidence=candidate.confidence,
                        strength=candidate.strength,
                        age_bars=candidate.age_bars,
                        validity=True,
                        historical_success_rate=candidate.historical_success_rate,
                        reliability=candidate.reliability,
                        current_context=candidate.current_context,
                        volume_confirmation=candidate.volume_confirmation,
                        trend_confirmation=candidate.trend_confirmation,
                        volatility=candidate.volatility,
                        market_regime=candidate.market_regime,
                        evidence=candidate.evidence,
                        source_event_type=candidate.source_event_type,
                        previous_confidence=previous.confidence,
                        previous_strength=previous.strength,
                    )
                )
            previous.confidence = candidate.confidence
            previous.strength = candidate.strength
            previous.valid = True
        for key, state in list(self._active_patterns.items()):
            if key[:3] != (observation.symbol, observation.exchange, observation.timeframe.value):
                continue
            if key not in seen_keys and state.valid:
                invalidated = PatternCandidate(
                    pattern_id=key[3],
                    pattern=state.pattern,
                    category=state.category,
                    confidence=state.confidence,
                    strength=state.strength,
                    age_bars=state.age_bars,
                    validity=False,
                    historical_success_rate=self._success_rate(state.pattern),
                    reliability=self._reliability(state.pattern, 0.0, 0.0),
                    current_context={},
                    volume_confirmation=0.0,
                    trend_confirmation=0.0,
                    volatility=0.0,
                    market_regime=None,
                    evidence={"reason": "pattern_lost_confirmation"},
                    source_event_type=observation.source_event_type,
                    previous_confidence=state.confidence,
                    previous_strength=state.strength,
                    reason="pattern_lost_confirmation",
                )
                self._stats[state.pattern]["invalidated"] += 1
                self._active_patterns.pop(key, None)
                published.append(invalidated)

        self._generated += len(published)
        return tuple(published)

    def _detect_patterns(
        self,
        observation: PatternObservation,
        candles: list[Candle],
    ) -> tuple[PatternCandidate, ...]:
        if len(candles) < 2:
            return ()
        current = candles[-1]
        previous = candles[-2]
        trend_confirmation = self._trend_confirmation(observation, candles)
        volume_confirmation = self._volume_confirmation(observation, candles)
        volatility = self._volatility_score(observation, candles)
        current_context = self._current_context(observation, trend_confirmation, volume_confirmation, volatility)
        candidates: list[PatternCandidate] = []

        candidates.extend(
            [
                self._bullish_engulfing(observation, current, previous, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._bearish_engulfing(observation, current, previous, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._doji(observation, current, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._hammer(observation, current, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._shooting_star(observation, current, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._breakout(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._breakdown(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._gap_up(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._gap_down(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._false_breakout(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._false_breakdown(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._volatility_squeeze(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._volatility_state(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._volume_spike(observation, current, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._volume_dry_up(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._trend_continuation(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._momentum_state(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
                self._accumulation_distribution(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility),
            ]
        )

        if observation.market_regime and trend_confirmation > 0.6:
            candidates.append(
                self._trend_exhaustion(observation, candles, current_context, trend_confirmation, volume_confirmation, volatility)
            )

        filtered = tuple(candidate for candidate in candidates if candidate.validity and candidate.confidence >= self._settings.min_confidence)
        return filtered

    def _latest_feature_context(self, symbol: str, exchange: str, timeframe: str) -> dict[str, Any]:
        exact_key = (symbol, exchange, timeframe, "FeaturesUpdated")
        exact = self._feature_context.get(exact_key)
        if exact is not None:
            return exact
        prefix = (symbol, exchange, timeframe)
        latest: dict[str, Any] | None = None
        latest_seen: datetime | None = None
        for key, value in self._feature_context.items():
            if key[:3] != prefix:
                continue
            observed_at = value.get("observed_at")
            if isinstance(observed_at, datetime) and (latest_seen is None or observed_at >= latest_seen):
                latest = value
                latest_seen = observed_at
        return latest or {}

    def _current_context(
        self,
        observation: PatternObservation,
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> dict[str, float | str | bool]:
        return {
            "trend_confirmation": trend_confirmation,
            "volume_confirmation": volume_confirmation,
            "volatility": volatility,
            "market_regime": observation.market_regime or "",
        }

    def _bullish_engulfing(
        self,
        observation: PatternObservation,
        current: Candle,
        previous: Candle,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        bullish = current.close > current.open and previous.close < previous.open
        body = abs(current.close - current.open)
        prev_body = abs(previous.close - previous.open)
        engulfs = current.open <= previous.close and current.close >= previous.open
        confidence = self._combine(0.3 if bullish and engulfs else 0.0, 0.3, trend_confirmation, volume_confirmation, 1.0 - volatility, body / max(current.high - current.low, 1e-9))
        return self._candidate(
            observation,
            current,
            pattern="Bullish Engulfing",
            category="candlestick",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=bullish and engulfs and body > prev_body * 0.75,
            context=context,
            evidence={
                "body": body,
                "previous_body": prev_body,
                "engulfs_previous": engulfs,
                "bullish": bullish,
            },
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _bearish_engulfing(
        self,
        observation: PatternObservation,
        current: Candle,
        previous: Candle,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        bearish = current.close < current.open and previous.close > previous.open
        body = abs(current.close - current.open)
        prev_body = abs(previous.close - previous.open)
        engulfs = current.open >= previous.close and current.close <= previous.open
        confidence = self._combine(0.3 if bearish and engulfs else 0.0, 0.3, trend_confirmation, volume_confirmation, 1.0 - volatility, body / max(current.high - current.low, 1e-9))
        return self._candidate(
            observation,
            current,
            pattern="Bearish Engulfing",
            category="candlestick",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=bearish and engulfs and body > prev_body * 0.75,
            context=context,
            evidence={
                "body": body,
                "previous_body": prev_body,
                "engulfs_previous": engulfs,
                "bearish": bearish,
            },
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _doji(
        self,
        observation: PatternObservation,
        current: Candle,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        body = abs(current.close - current.open)
        rng = max(current.high - current.low, 1e-9)
        ratio = body / rng
        confidence = max(0.0, 0.8 - ratio)
        return self._candidate(
            observation,
            current,
            pattern="Doji",
            category="candlestick",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=ratio <= 0.12,
            context=context,
            evidence={"body_ratio": ratio, "range": rng},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _hammer(
        self,
        observation: PatternObservation,
        current: Candle,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        body = abs(current.close - current.open)
        upper = current.high - max(current.open, current.close)
        lower = min(current.open, current.close) - current.low
        valid = lower >= body * 2.0 and upper <= body * 1.2
        confidence = self._combine(0.25 if valid else 0.0, 0.25, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            current,
            pattern="Hammer",
            category="reversal",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"body": body, "upper_wick": upper, "lower_wick": lower},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _shooting_star(
        self,
        observation: PatternObservation,
        current: Candle,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        body = abs(current.close - current.open)
        upper = current.high - max(current.open, current.close)
        lower = min(current.open, current.close) - current.low
        valid = upper >= body * 2.0 and lower <= body * 1.2
        confidence = self._combine(0.25 if valid else 0.0, 0.25, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            current,
            pattern="Shooting Star",
            category="reversal",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"body": body, "upper_wick": upper, "lower_wick": lower},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _breakout(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        recent_high = max(candle.high for candle in candles[-10:])
        close = candles[-1].close
        valid = close >= recent_high and volume_confirmation >= self._settings.volume_confirm_threshold
        confidence = self._combine(0.2 if valid else 0.0, 0.3, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            candles[-1],
            pattern="Bullish Breakout",
            category="chart",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"recent_high": recent_high, "close": close},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _breakdown(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        recent_low = min(candle.low for candle in candles[-10:])
        close = candles[-1].close
        valid = close <= recent_low and volume_confirmation >= self._settings.volume_confirm_threshold
        confidence = self._combine(0.2 if valid else 0.0, 0.3, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            candles[-1],
            pattern="Bearish Breakdown",
            category="chart",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"recent_low": recent_low, "close": close},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _volatility_squeeze(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        closes = [candle.close for candle in candles[-20:]]
        stddev = statistics.pstdev(closes) if len(closes) > 1 else 0.0
        rng = max(candle.high - candle.low for candle in candles[-20:])
        squeeze_score = stddev / rng if rng > 0 else 0.0
        valid = squeeze_score <= 0.35
        confidence = max(0.0, 0.85 - squeeze_score)
        return self._candidate(
            observation,
            candles[-1],
            pattern="Volatility Squeeze",
            category="volatility",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"stddev": stddev, "range": rng, "squeeze_score": squeeze_score},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _volatility_state(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        if volatility >= 0.65:
            pattern = "High Volatility"
            valid = True
        elif volatility <= 0.28:
            pattern = "Low Volatility"
            valid = True
        else:
            pattern = "Volatility State"
            valid = False
        confidence = max(0.0, 0.9 - abs(volatility - 0.5))
        return self._candidate(
            observation,
            candles[-1],
            pattern=pattern,
            category="volatility",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"volatility": volatility},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _volume_spike(
        self,
        observation: PatternObservation,
        current: Candle,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        recent = [candle.volume for candle in candles[-10:]]
        avg_volume = statistics.fmean(recent) if recent else current.volume
        ratio = current.volume / avg_volume if avg_volume > 0 else 1.0
        valid = ratio >= 1.5
        confidence = min(1.0, max(0.0, (ratio - 1.0) / 1.5))
        return self._candidate(
            observation,
            current,
            pattern="Volume Spike",
            category="volume",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"average_volume": avg_volume, "volume_ratio": ratio},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _volume_dry_up(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        recent = [candle.volume for candle in candles[-10:]]
        avg_volume = statistics.fmean(recent) if recent else candles[-1].volume
        ratio = candles[-1].volume / avg_volume if avg_volume > 0 else 0.0
        valid = ratio <= 0.72
        confidence = max(0.0, min(1.0, (0.9 - ratio) / 0.9))
        return self._candidate(
            observation,
            candles[-1],
            pattern="Volume Dry Up",
            category="volume",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"average_volume": avg_volume, "volume_ratio": ratio},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _trend_continuation(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        closes = [candle.close for candle in candles[-5:]]
        ascending = all(a <= b for a, b in zip(closes, closes[1:], strict=False))
        descending = all(a >= b for a, b in zip(closes, closes[1:], strict=False))
        if ascending:
            pattern = "Bull Trend Continuation"
            valid = trend_confirmation >= self._settings.trend_confirm_threshold
        elif descending:
            pattern = "Bear Trend Continuation"
            valid = trend_confirmation >= self._settings.trend_confirm_threshold
        else:
            pattern = "Trend Continuation"
            valid = False
        confidence = self._combine(0.2 if valid else 0.0, 0.25, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            candles[-1],
            pattern=pattern,
            category="continuation",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"ascending": ascending, "descending": descending, "closes": closes},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _momentum_state(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        closes = [candle.close for candle in candles[-6:]]
        if len(closes) < 3:
            valid = False
            pattern = "Momentum State"
            confidence = 0.0
        else:
            change = (closes[-1] - closes[0]) / max(closes[0], 1e-9)
            valid = abs(change) >= 0.01 and trend_confirmation >= 0.5
            if change > 0:
                pattern = "Strong Momentum"
            else:
                pattern = "Weak Momentum" if abs(change) < 0.015 else "Strong Momentum"
            confidence = min(1.0, abs(change) * 20.0 + trend_confirmation * 0.3 + volume_confirmation * 0.2)
        return self._candidate(
            observation,
            candles[-1],
            pattern=pattern,
            category="trend",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"closes": closes},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _gap_up(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        if len(candles) < 2:
            return self._candidate(
                observation,
                candles[-1],
                pattern="Gap Up",
                category="chart",
                confidence=0.0,
                strength=0.0,
                validity=False,
                context=context,
                evidence={"reason": "insufficient_history"},
                trend_confirmation=trend_confirmation,
                volume_confirmation=volume_confirmation,
                volatility=volatility,
            )
        previous = candles[-2]
        current = candles[-1]
        gap = current.open - previous.high
        valid = gap > 0 and current.close >= current.open
        confidence = min(1.0, max(0.0, gap / max(previous.close, 1e-9) * 20.0 + volume_confirmation * 0.2))
        return self._candidate(
            observation,
            current,
            pattern="Gap Up",
            category="chart",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"gap": gap, "previous_high": previous.high, "current_open": current.open},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _gap_down(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        if len(candles) < 2:
            return self._candidate(
                observation,
                candles[-1],
                pattern="Gap Down",
                category="chart",
                confidence=0.0,
                strength=0.0,
                validity=False,
                context=context,
                evidence={"reason": "insufficient_history"},
                trend_confirmation=trend_confirmation,
                volume_confirmation=volume_confirmation,
                volatility=volatility,
            )
        previous = candles[-2]
        current = candles[-1]
        gap = previous.low - current.open
        valid = gap > 0 and current.close <= current.open
        confidence = min(1.0, max(0.0, gap / max(previous.close, 1e-9) * 20.0 + volume_confirmation * 0.2))
        return self._candidate(
            observation,
            current,
            pattern="Gap Down",
            category="chart",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"gap": gap, "previous_low": previous.low, "current_open": current.open},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _false_breakout(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        recent_high = max(candle.high for candle in candles[-10:])
        current = candles[-1]
        valid = current.high > recent_high and current.close < recent_high and current.close < current.open
        confidence = self._combine(0.2 if valid else 0.0, 0.25, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            current,
            pattern="False Breakout",
            category="reversal",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"recent_high": recent_high, "high": current.high, "close": current.close},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _false_breakdown(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        recent_low = min(candle.low for candle in candles[-10:])
        current = candles[-1]
        valid = current.low < recent_low and current.close > recent_low and current.close > current.open
        confidence = self._combine(0.2 if valid else 0.0, 0.25, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            current,
            pattern="False Breakdown",
            category="reversal",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"recent_low": recent_low, "low": current.low, "close": current.close},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _accumulation_distribution(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        closes = [candle.close for candle in candles[-8:]]
        ranges = [candle.high - candle.low for candle in candles[-8:]]
        net_change = (closes[-1] - closes[0]) / max(closes[0], 1e-9) if len(closes) > 1 else 0.0
        range_compression = statistics.fmean(ranges) / max(closes[-1], 1e-9) if ranges else 0.0
        if net_change >= 0 and range_compression <= 0.02 and volume_confirmation >= 0.45:
            pattern = "Accumulation"
            valid = True
        elif net_change < 0 and range_compression <= 0.02 and volume_confirmation >= 0.45:
            pattern = "Distribution"
            valid = True
        else:
            pattern = "Accumulation/Distribution"
            valid = False
        confidence = min(1.0, max(0.0, (0.7 - range_compression * 10.0) + volume_confirmation * 0.2))
        return self._candidate(
            observation,
            candles[-1],
            pattern=pattern,
            category="trend",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={
                "net_change": net_change,
                "range_compression": range_compression,
                "closes": closes,
            },
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _trend_exhaustion(
        self,
        observation: PatternObservation,
        candles: list[Candle],
        context: dict[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        closes = [candle.close for candle in candles[-6:]]
        body = abs(candles[-1].close - candles[-1].open)
        wick_ratio = (candles[-1].high - candles[-1].low) / max(body, 1e-9)
        valid = wick_ratio > 4.0 and trend_confirmation > 0.6
        confidence = self._combine(0.15 if valid else 0.0, 0.2, trend_confirmation, volume_confirmation, 1.0 - volatility)
        return self._candidate(
            observation,
            candles[-1],
            pattern="Trend Exhaustion",
            category="trend",
            confidence=confidence,
            strength=confidence * 100.0,
            validity=valid,
            context=context,
            evidence={"closes": closes, "wick_ratio": wick_ratio},
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            volatility=volatility,
        )

    def _candidate(
        self,
        observation: PatternObservation,
        candle: Candle,
        *,
        pattern: str,
        category: str,
        confidence: float,
        strength: float,
        validity: bool,
        context: dict[str, float | str | bool],
        evidence: Mapping[str, float | str | bool],
        trend_confirmation: float,
        volume_confirmation: float,
        volatility: float,
    ) -> PatternCandidate:
        pattern_id = f"{pattern}:{observation.symbol}:{observation.timeframe.value}"
        history_rate = self._success_rate(pattern)
        reliability = self._reliability(pattern, confidence, context.get("trend_confirmation", 0.0))
        age_bars = self._age_for(pattern_id, observation)
        return PatternCandidate(
            pattern_id=pattern_id,
            pattern=pattern,
            category=category,
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            strength=round(max(0.0, min(100.0, strength)), 4),
            age_bars=age_bars,
            validity=validity and confidence >= self._settings.min_confidence,
            historical_success_rate=round(history_rate, 4),
            reliability=round(reliability, 4),
            current_context=context,
            volume_confirmation=round(max(0.0, min(1.0, volume_confirmation)), 4),
            trend_confirmation=round(max(0.0, min(1.0, trend_confirmation)), 4),
            volatility=round(max(0.0, min(1.0, volatility)), 4),
            market_regime=observation.market_regime,
            evidence=evidence,
            source_event_type=observation.source_event_type,
        )

    def _age_for(self, pattern_id: str, observation: PatternObservation) -> int:
        for key, state in self._active_patterns.items():
            if key[0] == observation.symbol and key[1] == observation.exchange and key[2] == observation.timeframe.value and key[3] == pattern_id:
                return state.age_bars + 1
        return 1

    def _success_rate(self, pattern: str) -> float:
        stats = self._stats[pattern]
        detections = stats["detections"]
        if detections <= 0:
            return 0.5
        return stats["confirmed"] / detections

    def _reliability(self, pattern: str, confidence: float, context_score: float) -> float:
        historical = self._success_rate(pattern)
        return max(0.0, min(1.0, (historical * 0.55) + (confidence * 0.25) + (context_score * 0.20)))

    def _trend_confirmation(self, observation: PatternObservation, candles: list[Candle]) -> float:
        closes = [candle.close for candle in candles[-8:]]
        if len(closes) < 2:
            return 0.5
        slope = closes[-1] - closes[0]
        price_change = abs(slope) / max(closes[0], 1e-9)
        direction = 1.0 if slope > 0 else 0.0
        indicators = observation.indicators
        indicator_bias = 0.0
        macd = indicators.get("MACD")
        if isinstance(macd, tuple) and len(macd) == 3:
            indicator_bias += 0.15 if macd[0] >= macd[1] else -0.15
        adx = indicators.get("ADX")
        if isinstance(adx, (int, float)):
            indicator_bias += min(float(adx) / 100.0, 0.2)
        ema = indicators.get("EMA")
        if isinstance(ema, (int, float)):
            indicator_bias += 0.1 if closes[-1] >= float(ema) else -0.1
        score = 0.35 + (direction * 0.35) + min(price_change * 4.0, 0.2) + indicator_bias
        return max(0.0, min(1.0, score))

    def _volume_confirmation(self, observation: PatternObservation, candles: list[Candle]) -> float:
        recent = [candle.volume for candle in candles[-10:]]
        avg = statistics.fmean(recent) if recent else candles[-1].volume
        ratio = candles[-1].volume / avg if avg > 0 else 1.0
        features = observation.features
        relative_volume = features.get("relative_volume")
        volume_ratio = features.get("volume_ratio")
        feature_ratio = 0.0
        for value in (relative_volume, volume_ratio):
            if isinstance(value, (int, float)):
                feature_ratio = max(feature_ratio, float(value))
        score = min(1.0, max(ratio, feature_ratio) / 2.0)
        return score

    def _volatility_score(self, observation: PatternObservation, candles: list[Candle]) -> float:
        current = candles[-1]
        avg_range = statistics.fmean([(candle.high - candle.low) for candle in candles[-10:]]) if candles else 0.0
        if avg_range <= 0:
            return 0.0
        ratio = (current.high - current.low) / avg_range
        indicators = observation.indicators
        atr = indicators.get("ATR")
        if isinstance(atr, (int, float)):
            ratio = max(ratio, float(atr) / max(current.close, 1e-9))
        return max(0.0, min(1.0, ratio / 2.5))

    def _combine(self, *parts: float) -> float:
        if not parts:
            return 0.0
        return max(0.0, min(1.0, sum(parts) / len(parts)))


__all__ = [
    "PatternCandidate",
    "PatternIntelligenceService",
    "PatternIntelligenceSettings",
    "PatternObservation",
]
