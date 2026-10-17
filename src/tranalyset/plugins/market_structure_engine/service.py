from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tranalyset.domain.common.value_objects import Price
from tranalyset.domain.services.market_structure_analyzer import MarketStructureAnalyzer
from tranalyset.market_data.aggregator import TimeframeAggregationEngine
from tranalyset.market_data.indicators import IndicatorCache
from tranalyset.market_data.models import Candle, Timeframe
from tranalyset.market_data.repository import MarketDataRepository
from tranalyset.plugins.feature_engine.contracts import FeatureSet
from tranalyset.plugins.indicator_engine.contracts import IndicatorsUpdatedPayload


@dataclass(frozen=True, slots=True)
class MarketStructureSettings:
    lookback_bars: int = 60
    swing_window: int = 2
    equality_tolerance: float = 0.0015
    structure_break_threshold: float = 0.002
    liquidity_sweep_threshold: float = 0.001
    fvg_threshold: float = 0.001
    min_confidence: float = 0.5

    def __post_init__(self) -> None:
        for field_name in ("lookback_bars", "swing_window"):
            if getattr(self, field_name) <= 0:
                msg = f"{field_name} must be greater than zero."
                raise ValueError(msg)
        for field_name in (
            "equality_tolerance",
            "structure_break_threshold",
            "liquidity_sweep_threshold",
            "fvg_threshold",
            "min_confidence",
        ):
            if getattr(self, field_name) < 0:
                msg = f"{field_name} must not be negative."
                raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MarketStructureEvaluation:
    symbol: str
    exchange: str
    timeframe: Timeframe
    observed_at: datetime
    structure: str
    internal_structure: str
    external_structure: str
    confidence: float
    swing_high: float | None
    swing_low: float | None
    higher_highs: int
    higher_lows: int
    lower_highs: int
    lower_lows: int
    equal_highs: int
    equal_lows: int
    break_of_structure: bool
    change_of_character: bool
    liquidity_zone: str | None
    premium_zone: bool
    discount_zone: bool
    market_regime: str | None
    detected: tuple[str, ...]
    evidence: dict[str, float | str | bool]
    source_event_type: str


@dataclass(frozen=True, slots=True)
class MarketStructureBatch:
    evaluation: MarketStructureEvaluation
    updated: dict[str, Any]
    structure_break: dict[str, Any] | None = None
    character_changed: dict[str, Any] | None = None
    liquidity_detected: dict[str, Any] | None = None


@dataclass(slots=True)
class _StructureState:
    structure: str = "unknown"
    character: str = "unknown"
    last_order_block_high: float | None = None
    last_order_block_low: float | None = None
    last_order_block_direction: str | None = None
    last_evaluated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MarketStructureObservation:
    symbol: str
    exchange: str
    timeframe: Timeframe
    observed_at: datetime
    source_event_type: str
    candles: tuple[Candle, ...]
    indicators: dict[str, Any]
    features: dict[str, Any]
    pattern_context: dict[str, Any]
    market_regime: str | None
    market_regime_confidence: float


class MarketStructureService:
    def __init__(
        self,
        *,
        repository: MarketDataRepository,
        indicator_cache: IndicatorCache,
        aggregation_engine: TimeframeAggregationEngine,
        settings: MarketStructureSettings | None = None,
    ) -> None:
        self._repository = repository
        self._indicator_cache = indicator_cache
        self._aggregation_engine = aggregation_engine
        self._settings = settings or MarketStructureSettings()
        self._lock = asyncio.Lock()
        self._seen_candles: dict[tuple[str, str, str], datetime] = {}
        self._indicator_context: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        self._feature_context: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        self._pattern_context: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        self._regime_context: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        self._state: dict[tuple[str, str, str], _StructureState] = defaultdict(_StructureState)
        self._event_counts = {
            "candle": 0,
            "indicator": 0,
            "feature": 0,
            "pattern": 0,
            "regime": 0,
        }
        self._evaluations = 0

    @property
    def evaluations(self) -> int:
        return self._evaluations

    async def update_indicator_context(self, payload: IndicatorsUpdatedPayload) -> None:
        key = (payload.symbol, payload.exchange, payload.timeframe)
        self._indicator_context[key] = {
            "indicators": dict(payload.indicators),
            "observed_at": payload.observed_at,
            "source_event_type": payload.source_event_type,
        }
        self._event_counts["indicator"] += 1

    async def update_feature_context(self, feature_set: FeatureSet) -> None:
        key = (feature_set.symbol, feature_set.exchange, feature_set.timeframe)
        self._feature_context[key] = {
            "feature_set_id": feature_set.feature_set_id,
            "features": dict(feature_set.features),
            "observed_at": feature_set.observed_at,
            "source_event_type": feature_set.source_event_type,
        }
        self._event_counts["feature"] += 1

    async def update_pattern_context(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        observed_at: datetime,
        pattern: str | None,
        confidence: float,
        source_event_type: str,
        valid: bool = True,
    ) -> None:
        key = (symbol, exchange, timeframe.value)
        context = dict(self._pattern_context.get(key, {}))
        if not valid:
            context.pop("pattern", None)
            context.pop("pattern_confidence", None)
        elif pattern is not None:
            context["pattern"] = pattern
            context["pattern_confidence"] = confidence
        context["observed_at"] = observed_at
        context["source_event_type"] = source_event_type
        self._pattern_context[key] = context
        self._event_counts["pattern"] += 1

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
        self._regime_context[key] = {
            "regime": regime,
            "confidence": confidence,
            "observed_at": observed_at,
            "source_event_type": source_event_type,
        }
        self._event_counts["regime"] += 1

    async def ingest_candle(self, candle: Candle, *, source_event_type: str) -> MarketStructureBatch | None:
        key = (candle.symbol, candle.exchange, candle.timeframe.value)
        async with self._lock:
            if self._seen_candles.get(key) == candle.timestamp:
                return None
            self._seen_candles[key] = candle.timestamp
            await self._repository.append_candle(candle)
            observation = await self._observation_for(candle, source_event_type=source_event_type)
            return self._evaluate_and_apply(observation)

    async def ingest_snapshot(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        source_event_type: str,
    ) -> MarketStructureBatch | None:
        candle = await self._repository.get_latest_candle(symbol, exchange, timeframe)
        if candle is None:
            return None
        observation = await self._observation_for(candle, source_event_type=source_event_type)
        async with self._lock:
            return self._evaluate_and_apply(observation)

    async def health_snapshot(self) -> dict[str, int]:
        return dict(self._event_counts)

    async def _observation_for(self, candle: Candle, *, source_event_type: str) -> MarketStructureObservation:
        key = (candle.symbol, candle.exchange, candle.timeframe.value)
        candles = tuple(
            await self._repository.get_candles(
                candle.symbol,
                candle.exchange,
                candle.timeframe,
                limit=self._settings.lookback_bars,
            )
        )
        indicators = dict(self._indicator_context.get(key, {}).get("indicators", {}))
        features = dict(self._feature_context.get(key, {}).get("features", {}))
        pattern_context = dict(self._pattern_context.get(key, {}))
        regime_context = self._regime_context.get(key, {})
        regime = regime_context.get("regime")
        regime_confidence = float(regime_context.get("confidence", 0.0) or 0.0)
        return MarketStructureObservation(
            symbol=candle.symbol,
            exchange=candle.exchange,
            timeframe=candle.timeframe,
            observed_at=candle.timestamp,
            source_event_type=source_event_type,
            candles=candles,
            indicators=indicators,
            features=features,
            pattern_context=pattern_context,
            market_regime=regime if isinstance(regime, str) else None,
            market_regime_confidence=regime_confidence,
        )

    def _evaluate_and_apply(self, observation: MarketStructureObservation) -> MarketStructureBatch:
        evaluation = self._evaluate(observation)
        state_key = (observation.symbol, observation.exchange, observation.timeframe.value)
        state = self._state[state_key]
        structure_break = self._structure_break_payload(observation, evaluation, state)
        character_changed = self._character_changed_payload(observation, evaluation, state)
        liquidity_detected = self._liquidity_payload(observation, evaluation)
        state.structure = evaluation.structure
        state.character = evaluation.internal_structure
        state.last_evaluated_at = observation.observed_at
        if structure_break is not None and structure_break.get("direction") == "bullish":
            state.last_order_block_direction = "bearish"
        elif structure_break is not None and structure_break.get("direction") == "bearish":
            state.last_order_block_direction = "bullish"
        self._evaluations += 1
        return MarketStructureBatch(
            evaluation=evaluation,
            updated=evaluation_to_payload(evaluation),
            structure_break=structure_break,
            character_changed=character_changed,
            liquidity_detected=liquidity_detected,
        )

    def _evaluate(self, observation: MarketStructureObservation) -> MarketStructureEvaluation:
        candles = observation.candles
        if not candles:
            raise ValueError("Market structure evaluation requires at least one candle.")

        prices = [Price(candle.close) for candle in candles]
        lookback = min(len(prices), self._settings.lookback_bars)
        if lookback <= 0:
            lookback = len(prices)
        result = MarketStructureAnalyzer.analyze(prices, lookback=lookback)

        swing_highs, swing_lows = self._swing_points(candles)
        swing_high = swing_highs[-1][1] if swing_highs else None
        swing_low = swing_lows[-1][1] if swing_lows else None
        higher_highs = self._count_rising(swing_highs)
        higher_lows = self._count_rising(swing_lows)
        lower_highs = self._count_falling(swing_highs)
        lower_lows = self._count_falling(swing_lows)
        equal_highs = self._count_equals(swing_highs)
        equal_lows = self._count_equals(swing_lows)

        structure = self._structure_label(result.structure_type)
        internal_structure = self._internal_structure_label(higher_highs, higher_lows, lower_highs, lower_lows)
        external_structure = self._external_structure_label(result.structure_type)
        trend_confirmation = self._trend_confirmation(observation)
        volume_confirmation = self._volume_confirmation(observation)
        liquidity = self._liquidity_score(observation, equal_highs, equal_lows)
        volatility = self._volatility_score(observation)
        detected = self._detected_labels(
            observation,
            swing_high=swing_high,
            swing_low=swing_low,
            higher_highs=higher_highs,
            higher_lows=higher_lows,
            lower_highs=lower_highs,
            lower_lows=lower_lows,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
            result=result.structure_type,
        )
        confidence = self._confidence(
            float(result.confidence.value),
            trend_confirmation=trend_confirmation,
            volume_confirmation=volume_confirmation,
            liquidity=liquidity,
            volatility=volatility,
            detection_count=len(detected),
            market_regime_confidence=observation.market_regime_confidence,
        )
        premium_zone, discount_zone = self._premium_discount(observation)
        evidence = {
            "structure_type": result.structure_type,
            "change_ratio": float(result.supporting_factors.get("change_ratio", 0)),
            "range_width": float(result.supporting_factors.get("range_width", 0)),
            "trend_confirmation": trend_confirmation,
            "volume_confirmation": volume_confirmation,
            "liquidity": liquidity,
            "volatility": volatility,
            "market_regime": observation.market_regime or "",
            "market_regime_confidence": observation.market_regime_confidence,
            "premium_zone": premium_zone,
            "discount_zone": discount_zone,
            "pattern": observation.pattern_context.get("pattern", ""),
        }
        break_of_structure = "Break of Structure" in detected
        change_of_character = "Change of Character" in detected
        liquidity_zone = None
        if "Liquidity Grab" in detected:
            liquidity_zone = "liquidity_grab"
        elif "Liquidity Sweep" in detected:
            liquidity_zone = "liquidity_sweep"
        return MarketStructureEvaluation(
            symbol=observation.symbol,
            exchange=observation.exchange,
            timeframe=observation.timeframe,
            observed_at=observation.observed_at,
            structure=structure,
            internal_structure=internal_structure,
            external_structure=external_structure,
            confidence=confidence,
            swing_high=swing_high,
            swing_low=swing_low,
            higher_highs=higher_highs,
            higher_lows=higher_lows,
            lower_highs=lower_highs,
            lower_lows=lower_lows,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
            break_of_structure=break_of_structure,
            change_of_character=change_of_character,
            liquidity_zone=liquidity_zone,
            premium_zone=premium_zone,
            discount_zone=discount_zone,
            market_regime=observation.market_regime,
            detected=detected,
            evidence=evidence,
            source_event_type=observation.source_event_type,
        )

    def _swing_points(self, candles: tuple[Candle, ...]) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
        window = min(self._settings.swing_window, max(1, len(candles) // 2))
        highs: list[tuple[int, float]] = []
        lows: list[tuple[int, float]] = []
        for index in range(window, len(candles) - window):
            candle = candles[index]
            left = candles[index - window : index]
            right = candles[index + 1 : index + window + 1]
            if all(candle.high > other.high for other in (*left, *right)):
                highs.append((index, float(candle.high)))
            if all(candle.low < other.low for other in (*left, *right)):
                lows.append((index, float(candle.low)))
        return highs, lows

    def _count_rising(self, points: list[tuple[int, float]]) -> int:
        if len(points) < 2:
            return 0
        count = 0
        for previous, current in zip(points, points[1:], strict=False):
            if current[1] > previous[1]:
                count += 1
        return count

    def _count_falling(self, points: list[tuple[int, float]]) -> int:
        if len(points) < 2:
            return 0
        count = 0
        for previous, current in zip(points, points[1:], strict=False):
            if current[1] < previous[1]:
                count += 1
        return count

    def _count_equals(self, points: list[tuple[int, float]]) -> int:
        count = 0
        for previous, current in zip(points, points[1:], strict=False):
            if abs(current[1] - previous[1]) / max(previous[1], 1e-9) <= self._settings.equality_tolerance:
                count += 1
        return count

    def _structure_label(self, structure_type: str) -> str:
        mapping = {
            "trending_up": "Bull Structure",
            "trending_down": "Bear Structure",
            "breakout": "Breakout Structure",
            "breakdown": "Breakdown Structure",
            "ranging": "Range Structure",
        }
        return mapping.get(structure_type, "Range Structure")

    def _internal_structure_label(
        self,
        higher_highs: int,
        higher_lows: int,
        lower_highs: int,
        lower_lows: int,
    ) -> str:
        if higher_highs and higher_lows and higher_highs >= lower_highs:
            return "Trend Structure"
        if lower_highs and lower_lows and lower_highs >= higher_highs:
            return "Counter Trend Structure"
        return "Internal Structure"

    def _external_structure_label(self, structure_type: str) -> str:
        if structure_type in {"trending_up", "breakout"}:
            return "External Bull Structure"
        if structure_type in {"trending_down", "breakdown"}:
            return "External Bear Structure"
        return "External Range Structure"

    def _trend_confirmation(self, observation: MarketStructureObservation) -> float:
        candles = observation.candles
        closes = [candle.close for candle in candles[-8:]]
        if len(closes) < 2:
            return 0.5
        change = (closes[-1] - closes[0]) / max(closes[0], 1e-9)
        indicators = observation.indicators
        score = 0.5 + min(abs(change) * 4.0, 0.25)
        ema = indicators.get("EMA")
        if isinstance(ema, (int, float)):
            score += 0.1 if closes[-1] >= float(ema) else -0.1
        macd = indicators.get("MACD")
        if isinstance(macd, tuple) and len(macd) >= 2:
            score += 0.08 if macd[0] >= macd[1] else -0.08
        adx = indicators.get("ADX")
        if isinstance(adx, (int, float)):
            score += min(float(adx) / 100.0, 0.12)
        pattern = observation.pattern_context.get("pattern_confidence")
        if isinstance(pattern, (int, float)):
            score += min(float(pattern) * 0.1, 0.1)
        return max(0.0, min(1.0, score))

    def _volume_confirmation(self, observation: MarketStructureObservation) -> float:
        candles = observation.candles
        recent = [candle.volume for candle in candles[-10:]]
        average = statistics.fmean(recent) if recent else 0.0
        current = candles[-1].volume
        if average <= 0:
            return 0.5
        ratio = current / average
        feature_ratio = 0.0
        for key in ("relative_volume", "volume_ratio"):
            value = observation.features.get(key)
            if isinstance(value, (int, float)):
                feature_ratio = max(feature_ratio, float(value))
        return max(0.0, min(1.0, max(ratio, feature_ratio) / 2.0))

    def _volatility_score(self, observation: MarketStructureObservation) -> float:
        candles = observation.candles
        ranges = [candle.high - candle.low for candle in candles[-10:]]
        if not ranges:
            return 0.0
        current = candles[-1]
        average = statistics.fmean(ranges)
        if average <= 0:
            return 0.0
        score = (current.high - current.low) / average
        atr = observation.indicators.get("ATR")
        if isinstance(atr, (int, float)):
            score = max(score, float(atr) / max(current.close, 1e-9))
        return max(0.0, min(1.0, score / 2.5))

    def _liquidity_score(self, observation: MarketStructureObservation, equal_highs: int, equal_lows: int) -> float:
        liquidity = 0.25
        liquidity += min((equal_highs + equal_lows) * 0.15, 0.45)
        liquidity += min(self._volume_confirmation(observation) * 0.3, 0.3)
        return max(0.0, min(1.0, liquidity))

    def _detected_labels(
        self,
        observation: MarketStructureObservation,
        *,
        swing_high: float | None,
        swing_low: float | None,
        higher_highs: int,
        higher_lows: int,
        lower_highs: int,
        lower_lows: int,
        equal_highs: int,
        equal_lows: int,
        result: str,
    ) -> tuple[str, ...]:
        labels: list[str] = []
        if swing_high is not None:
            labels.append("Swing High")
        if swing_low is not None:
            labels.append("Swing Low")
        current = observation.candles[-1]
        if swing_high is not None and current.close > swing_high * (1 + self._settings.structure_break_threshold):
            labels.append("Higher High")
        if swing_low is not None and current.low > swing_low:
            labels.append("Higher Low")
        if swing_high is not None and current.high < swing_high:
            labels.append("Lower High")
        if swing_low is not None and current.close < swing_low * (1 - self._settings.structure_break_threshold):
            labels.append("Lower Low")
        if higher_highs:
            labels.append("Higher High")
        if higher_lows:
            labels.append("Higher Low")
        if lower_highs:
            labels.append("Lower High")
        if lower_lows:
            labels.append("Lower Low")
        previous = observation.candles[-2] if len(observation.candles) >= 2 else None
        if swing_high is not None and current.close > swing_high * (1 + self._settings.structure_break_threshold):
            labels.append("Break of Structure")
        if swing_low is not None and current.close < swing_low * (1 - self._settings.structure_break_threshold):
            labels.append("Change of Character")
        if equal_highs:
            labels.append("Equal High")
        if equal_lows:
            labels.append("Equal Low")
        if equal_highs and current.high > (swing_high or current.high) * (1 + self._settings.liquidity_sweep_threshold) and current.close < (swing_high or current.close):
            labels.extend(["Liquidity Grab", "Liquidity Sweep"])
        if equal_lows and current.low < (swing_low or current.low) * (1 - self._settings.liquidity_sweep_threshold) and current.close > (swing_low or current.close):
            labels.extend(["Liquidity Grab", "Liquidity Sweep"])
        if len(observation.candles) >= 3:
            first = observation.candles[-3]
            if current.low > first.high * (1 + self._settings.fvg_threshold):
                labels.extend(["Fair Value Gap", "Imbalance"])
            if current.high < first.low * (1 - self._settings.fvg_threshold):
                labels.extend(["Fair Value Gap", "Imbalance"])
        if result in {"trending_up", "trending_down"}:
            labels.append("Trend Structure")
        if result in {"breakout", "breakdown"}:
            labels.append("External Structure")
        if previous is not None:
            bullish_candle = previous.close > previous.open
            bearish_candle = previous.close < previous.open
            if "Break of Structure" in labels and bearish_candle:
                labels.append("Order Block")
            if "Change of Character" in labels and bullish_candle:
                labels.append("Breaker Block")
            if ("Break of Structure" in labels or "Change of Character" in labels) and abs(current.close - previous.close) / max(previous.close, 1e-9) < 0.004:
                labels.append("Mitigation Block")
        if observation.candles:
            close = observation.candles[-1].close
            high = max(candle.high for candle in observation.candles[-20:])
            low = min(candle.low for candle in observation.candles[-20:])
            midpoint = low + ((high - low) / 2.0)
            if close >= midpoint:
                labels.append("Premium Zone")
            else:
                labels.append("Discount Zone")
        return tuple(dict.fromkeys(labels))

    def _premium_discount(self, observation: MarketStructureObservation) -> tuple[bool, bool]:
        candles = observation.candles[-20:]
        if not candles:
            return False, False
        high = max(candle.high for candle in candles)
        low = min(candle.low for candle in candles)
        midpoint = low + ((high - low) / 2.0)
        close = candles[-1].close
        return close >= midpoint, close < midpoint

    def _confidence(
        self,
        base: float,
        *,
        trend_confirmation: float,
        volume_confirmation: float,
        liquidity: float,
        volatility: float,
        detection_count: int,
        market_regime_confidence: float,
    ) -> float:
        score = (
            (base * 0.45)
            + (trend_confirmation * 0.15)
            + (volume_confirmation * 0.1)
            + (liquidity * 0.1)
            + ((1.0 - volatility) * 0.1)
            + (min(detection_count, 6) * 0.01)
            + (market_regime_confidence * 0.1)
        )
        return max(self._settings.min_confidence, min(1.0, score))

    def _structure_break_payload(
        self,
        observation: MarketStructureObservation,
        evaluation: MarketStructureEvaluation,
        state: _StructureState,
    ) -> dict[str, Any] | None:
        if not evaluation.break_of_structure:
            return None
        direction = "bullish" if evaluation.structure in {"Bull Structure", "Breakout Structure"} else "bearish"
        level = evaluation.swing_high if direction == "bullish" else evaluation.swing_low
        if level is None:
            level = observation.candles[-1].close
        if state.structure == evaluation.structure and state.last_order_block_direction == direction:
            return None
        return {
            "break_type": "Break of Structure",
            "direction": direction,
            "level": level,
            "confidence": evaluation.confidence,
            "previous_structure": state.structure if state.structure != "unknown" else None,
            "source_event_type": observation.source_event_type,
            "observed_at": observation.observed_at.isoformat(),
            "evidence": dict(evaluation.evidence),
        }

    def _character_changed_payload(
        self,
        observation: MarketStructureObservation,
        evaluation: MarketStructureEvaluation,
        state: _StructureState,
    ) -> dict[str, Any] | None:
        if not evaluation.change_of_character and state.character == evaluation.internal_structure:
            return None
        if state.character == evaluation.internal_structure:
            return None
        return {
            "previous_character": state.character if state.character != "unknown" else None,
            "character": evaluation.internal_structure,
            "confidence": evaluation.confidence,
            "source_event_type": observation.source_event_type,
            "observed_at": observation.observed_at.isoformat(),
            "evidence": dict(evaluation.evidence),
        }

    def _liquidity_payload(
        self,
        observation: MarketStructureObservation,
        evaluation: MarketStructureEvaluation,
    ) -> dict[str, Any] | None:
        if "Liquidity Grab" not in evaluation.detected and "Liquidity Sweep" not in evaluation.detected:
            return None
        side = "buy_side" if evaluation.swing_high is not None and observation.candles[-1].close < evaluation.swing_high else "sell_side"
        level = evaluation.swing_high if side == "buy_side" else evaluation.swing_low
        if level is None:
            level = observation.candles[-1].close
        liquidity_type = "Liquidity Sweep" if "Liquidity Sweep" in evaluation.detected else "Liquidity Grab"
        return {
            "liquidity_type": liquidity_type,
            "side": side,
            "level": level,
            "confidence": evaluation.confidence,
            "source_event_type": observation.source_event_type,
            "observed_at": observation.observed_at.isoformat(),
            "evidence": dict(evaluation.evidence),
        }


def evaluation_to_payload(evaluation: MarketStructureEvaluation) -> dict[str, Any]:
    return {
        "symbol": evaluation.symbol,
        "exchange": evaluation.exchange,
        "timeframe": evaluation.timeframe.value,
        "observed_at": evaluation.observed_at.isoformat(),
        "structure": evaluation.structure,
        "internal_structure": evaluation.internal_structure,
        "external_structure": evaluation.external_structure,
        "confidence": evaluation.confidence,
        "swing_high": evaluation.swing_high,
        "swing_low": evaluation.swing_low,
        "higher_highs": evaluation.higher_highs,
        "higher_lows": evaluation.higher_lows,
        "lower_highs": evaluation.lower_highs,
        "lower_lows": evaluation.lower_lows,
        "equal_highs": evaluation.equal_highs,
        "equal_lows": evaluation.equal_lows,
        "break_of_structure": evaluation.break_of_structure,
        "character_change": evaluation.change_of_character,
        "liquidity_zone": evaluation.liquidity_zone,
        "premium_zone": evaluation.premium_zone,
        "discount_zone": evaluation.discount_zone,
        "market_regime": evaluation.market_regime,
        "detected": list(evaluation.detected),
        "evidence": dict(evaluation.evidence),
        "source_event_type": evaluation.source_event_type,
    }


__all__ = [
    "MarketStructureBatch",
    "MarketStructureEvaluation",
    "MarketStructureObservation",
    "MarketStructureService",
    "MarketStructureSettings",
]
