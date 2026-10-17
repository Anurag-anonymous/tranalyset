from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import sqrt
from typing import Any, Protocol

from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED, MarketContextUpdatedPayload
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED, IndicatorsUpdatedPayload
from tranalyset.plugins.market_structure_engine.contracts import MarketStructureUpdatedPayload
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PATTERN_UPDATED,
    PatternDetectedPayload,
    PatternInvalidatedPayload,
    PatternUpdatedPayload,
)
from tranalyset.plugins.probability_engine.contracts import (
    ConfidenceUpdatedPayload,
    ProbabilityUpdatedPayload,
)
from tranalyset.plugins.market_structure_engine.contracts import (
    CHARACTER_CHANGED,
    LIQUIDITY_DETECTED,
    MARKET_STRUCTURE_UPDATED,
    STRUCTURE_BREAK,
)
from tranalyset.plugins.risk.contracts import (
    POSITION_SIZE_CALCULATED,
    RISK_APPROVED,
    RISK_REJECTED,
    STOP_LOSS_UPDATED,
    PositionSizeCalculatedPayload,
    RiskApprovedPayload,
    RiskRejectedPayload,
    StopLossUpdatedPayload,
)
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED, TradeHealthUpdatedPayload

ProbabilityKey = tuple[str, str, str, str]
GLOBAL = "GLOBAL"

OUTCOMES = (
    "bullish",
    "bearish",
    "trend_continuation",
    "trend_reversal",
    "breakout",
    "breakdown",
)


@dataclass(frozen=True, slots=True)
class ProbabilityEngineSettings:
    history_limit: int = 180
    prior_strength: float = 2.0
    update_delta: float = 0.03
    confidence_delta: float = 0.02
    weights: Mapping[str, float] = field(default_factory=dict)
    max_tracked_keys: int = 10_000

    def __post_init__(self) -> None:
        if self.history_limit <= 0:
            msg = "history_limit must be greater than zero."
            raise ValueError(msg)
        if self.max_tracked_keys <= 0:
            msg = "max_tracked_keys must be greater than zero."
            raise ValueError(msg)
        if self.prior_strength <= 0.0:
            msg = "prior_strength must be greater than zero."
            raise ValueError(msg)
        if self.update_delta < 0.0:
            msg = "update_delta must not be negative."
            raise ValueError(msg)
        if self.confidence_delta < 0.0:
            msg = "confidence_delta must not be negative."
            raise ValueError(msg)


@dataclass(slots=True)
class _Posterior:
    alpha: float
    beta: float


@dataclass(slots=True)
class ProbabilityEvidence:
    trade_id: str
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    posteriors: dict[str, _Posterior] = field(default_factory=dict)
    source_events: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reasoning_factors: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_count: int = 0

    def touch(self, *, source_event_type: str, observed_at: datetime, history_limit: int) -> None:
        self.observed_at = observed_at.astimezone(UTC)
        self.source_events.append(source_event_type)
        if len(self.source_events) > history_limit:
            del self.source_events[: len(self.source_events) - history_limit]

    def note(self, message: str, *, history_limit: int) -> None:
        if not message:
            return
        self.notes.append(message)
        if len(self.notes) > history_limit:
            del self.notes[: len(self.notes) - history_limit]

    def ensure_priors(self, settings: ProbabilityEngineSettings) -> None:
        if self.posteriors:
            return
        prior = settings.prior_strength / 2.0
        self.posteriors = {outcome: _Posterior(alpha=prior, beta=prior) for outcome in OUTCOMES}


@dataclass(frozen=True, slots=True)
class ProbabilitySnapshot:
    trade_id: str
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    bullish_probability: float
    bearish_probability: float
    trend_continuation_probability: float
    trend_reversal_probability: float
    breakout_probability: float
    breakdown_probability: float
    probability_distribution: Mapping[str, float]
    confidence_lower: float
    confidence_upper: float
    confidence: float
    reasoning_metadata: Mapping[str, Any]
    source_event_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_probability_payload(self) -> ProbabilityUpdatedPayload:
        return ProbabilityUpdatedPayload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            bullish_probability=self.bullish_probability,
            bearish_probability=self.bearish_probability,
            trend_continuation_probability=self.trend_continuation_probability,
            trend_reversal_probability=self.trend_reversal_probability,
            breakout_probability=self.breakout_probability,
            breakdown_probability=self.breakdown_probability,
            probability_distribution=self.probability_distribution,
            confidence_lower=self.confidence_lower,
            confidence_upper=self.confidence_upper,
            reasoning_metadata=self.reasoning_metadata,
            source_event_type=self.source_event_type,
            metadata=self.metadata,
        )

    def to_confidence_payload(self, *, previous_confidence: float | None) -> ConfidenceUpdatedPayload:
        return ConfidenceUpdatedPayload(
            trade_id=self.trade_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.timeframe,
            observed_at=self.observed_at,
            confidence=self.confidence,
            previous_confidence=previous_confidence,
            confidence_lower=self.confidence_lower,
            confidence_upper=self.confidence_upper,
            reasoning_metadata=self.reasoning_metadata,
            source_event_type=self.source_event_type,
            metadata=self.metadata,
        )


class ProbabilityModel(Protocol):
    def evaluate(
        self,
        evidence: ProbabilityEvidence,
        settings: ProbabilityEngineSettings,
    ) -> ProbabilitySnapshot:
        ...


class BayesianProbabilityModel:
    def evaluate(self, evidence: ProbabilityEvidence, settings: ProbabilityEngineSettings) -> ProbabilitySnapshot:
        evidence.ensure_priors(settings)
        outcomes = evidence.posteriors
        probabilities = {name: self._posterior_probability(outcomes[name]) for name in OUTCOMES}
        distribution = self._normalized_distribution(probabilities)
        bullish = probabilities["bullish"]
        bearish = probabilities["bearish"]
        continuation = probabilities["trend_continuation"]
        reversal = probabilities["trend_reversal"]
        breakout = probabilities["breakout"]
        breakdown = probabilities["breakdown"]
        confidence_lower, confidence_upper = self._interval(outcomes["bullish"])
        confidence = self._confidence(confidence_lower, confidence_upper, outcomes["bullish"])
        reasoning_metadata = {
            "model": type(self).__name__,
            "bayesian": True,
            "evidence_count": evidence.evidence_count,
            "source_events": tuple(evidence.source_events),
            "notes": tuple(evidence.notes),
            "factors": dict(sorted(evidence.reasoning_factors.items())),
            "posterior": {
                name: {
                    "alpha": posterior.alpha,
                    "beta": posterior.beta,
                    "probability": probabilities[name],
                }
                for name, posterior in outcomes.items()
            },
            "prior_strength": settings.prior_strength,
        }
        return ProbabilitySnapshot(
            trade_id=evidence.trade_id,
            symbol=evidence.symbol,
            exchange=evidence.exchange,
            timeframe=evidence.timeframe,
            observed_at=evidence.observed_at,
            bullish_probability=bullish,
            bearish_probability=bearish,
            trend_continuation_probability=continuation,
            trend_reversal_probability=reversal,
            breakout_probability=breakout,
            breakdown_probability=breakdown,
            probability_distribution=distribution,
            confidence_lower=confidence_lower,
            confidence_upper=confidence_upper,
            confidence=confidence,
            reasoning_metadata=reasoning_metadata,
            source_event_type=evidence.source_events[-1] if evidence.source_events else "ProbabilityEngine",
            metadata=evidence.metadata,
        )

    def _posterior_probability(self, posterior: _Posterior) -> float:
        total = posterior.alpha + posterior.beta
        return posterior.alpha / total if total > 0.0 else 0.5

    def _normalized_distribution(self, probabilities: Mapping[str, float]) -> dict[str, float]:
        total = sum(probabilities.values())
        if total <= 0.0:
            return {name: 1.0 / len(probabilities) for name in probabilities}
        return {name: value / total for name, value in probabilities.items()}

    def _interval(self, posterior: _Posterior) -> tuple[float, float]:
        total = posterior.alpha + posterior.beta
        if total <= 0.0:
            return (0.0, 1.0)
        mean = posterior.alpha / total
        variance = (posterior.alpha * posterior.beta) / ((total * total) * (total + 1.0))
        width = 1.96 * sqrt(max(0.0, variance))
        lower = max(0.0, mean - width)
        upper = min(1.0, mean + width)
        return (lower, upper)

    def _confidence(self, lower: float, upper: float, posterior: _Posterior) -> float:
        spread = max(0.0, upper - lower)
        mass = posterior.alpha + posterior.beta
        mass_score = mass / (mass + 4.0)
        spread_score = 1.0 - spread
        return max(0.0, min(1.0, (mass_score * 0.7) + (spread_score * 0.3)))


class ProbabilityEngineService:
    def __init__(
        self,
        *,
        settings: ProbabilityEngineSettings | None = None,
        model: ProbabilityModel | None = None,
    ) -> None:
        self._settings = settings or ProbabilityEngineSettings()
        self._model = model or BayesianProbabilityModel()
        self._evidence_by_key: OrderedDict[ProbabilityKey, ProbabilityEvidence] = OrderedDict()
        self._snapshot_by_key: dict[ProbabilityKey, ProbabilitySnapshot] = {}
        self._event_counts = {
            str(MARKET_CONTEXT_UPDATED): 0,
            str(TRADE_HEALTH_UPDATED): 0,
            str(PATTERN_DETECTED): 0,
            str(PATTERN_UPDATED): 0,
            str(PATTERN_INVALIDATED): 0,
            str(INDICATORS_UPDATED): 0,
            str(MARKET_STRUCTURE_UPDATED): 0,
            str(STRUCTURE_BREAK): 0,
            str(CHARACTER_CHANGED): 0,
            str(LIQUIDITY_DETECTED): 0,
            str(RISK_APPROVED): 0,
            str(RISK_REJECTED): 0,
            str(POSITION_SIZE_CALCULATED): 0,
            str(STOP_LOSS_UPDATED): 0,
        }
        self._generated = 0

    @property
    def generated(self) -> int:
        return self._generated

    async def observe_market_context(
        self,
        payload: MarketContextUpdatedPayload | Mapping[str, Any],
    ) -> tuple[ProbabilitySnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = _key_from_payload(data)
        evidence = self._evidence_for_key(key, observed_at=_timestamp(data))
        context_confidence = _number(data, "context_confidence", default=0.5)
        context_quality = _number(data, "context_quality", default=0.5)
        evidence.metadata.update(
            {
                "context_confidence": context_confidence,
                "context_quality": context_quality,
                "market_context": dict(data.get("market_context", {})),
                "sector_context": dict(data.get("sector_context", {})),
                "session_context": dict(data.get("session_context", {})),
            }
        )
        evidence.reasoning_factors.update(
            {
                "context_trend": _state_support(_text(data.get("trend_context", {}), "state", default=""), positive=("bull", "up"), negative=("bear", "down")),
                "context_momentum": _state_support(_text(data.get("momentum_context", {}), "state", default=""), positive=("strong",), negative=("weak", "neutral")),
                "context_volatility": _volatility_support(_text(data.get("volatility_context", {}), "state", default="")),
                "context_volume": _state_support(_text(data.get("volume_context", {}), "state", default=""), positive=("expanding",), negative=("contracting",)),
                "context_liquidity": _state_support(_text(data.get("liquidity_context", {}), "state", default=""), positive=("high",), negative=("low",)),
                "context_market": _market_state_support(_text(data.get("market_context", {}), "state", default="")),
                "context_sector": _state_support(_text(data.get("sector_context", {}), "state", default=""), positive=("supportive", "strong"), negative=("weak",)),
                "context_session": _state_support(_text(data.get("session_context", {}), "state", default=""), positive=("regular", "opening", "closing"), negative=("unknown",)),
                "context_risk": _state_support(_text(data.get("risk_context", {}), "state", default=""), positive=("low", "moderate"), negative=("high",)),
            }
        )
        note = (
            f"context trend={_text(data.get('trend_context', {}), 'state', default='unknown')}, "
            f"market={_text(data.get('market_context', {}), 'state', default='unknown')}, "
            f"risk={_text(data.get('risk_context', {}), 'state', default='unknown')}"
        )
        self._apply_supports(
            evidence,
            source_event_type=str(MARKET_CONTEXT_UPDATED),
            observed_at=evidence.observed_at,
            note=note,
            strength=max(context_confidence, context_quality),
            supports=self._context_supports(data),
        )
        self._event_counts[str(MARKET_CONTEXT_UPDATED)] += 1
        return self._snapshots_for([key], str(MARKET_CONTEXT_UPDATED))

    async def observe_trade_health(
        self,
        payload: TradeHealthUpdatedPayload | Mapping[str, Any],
    ) -> tuple[ProbabilitySnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = _key_from_payload(data)
        evidence = self._evidence_for_key(key, observed_at=_timestamp(data))
        health_score = _number(data, "health_score", default=50.0) / 100.0
        confidence = max(_number(data, "confidence_lower", default=0.5), _number(data, "confidence_upper", default=0.5))
        evidence.reasoning_factors.update(
            {
                "trade_health": health_score,
                "trade_trend": health_score,
                "trade_continuation": health_score,
                "trade_reversal": 1.0 - health_score,
            }
        )
        note = f"trade_health={health_score:.3f}"
        self._apply_supports(
            evidence,
            source_event_type=str(TRADE_HEALTH_UPDATED),
            observed_at=evidence.observed_at,
            note=note,
            strength=max(health_score, confidence),
            supports={
                "bullish": health_score,
                "bearish": 1.0 - health_score,
                "trend_continuation": health_score,
                "trend_reversal": 1.0 - health_score,
                "breakout": 0.5 * health_score + 0.25,
                "breakdown": 1.0 - health_score,
            },
        )
        self._event_counts[str(TRADE_HEALTH_UPDATED)] += 1
        return self._snapshots_for([key], str(TRADE_HEALTH_UPDATED))

    async def observe_pattern(
        self,
        payload: PatternDetectedPayload | PatternUpdatedPayload | PatternInvalidatedPayload | Mapping[str, Any],
    ) -> tuple[ProbabilitySnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = _key_from_payload(data)
        evidence = self._evidence_for_key(key, observed_at=_timestamp(data))
        pattern_text = _text(data, "pattern", default="")
        category = _text(data, "category", default="")
        validity = data.get("validity", True) is not False and not str(data.get("source_event_type", "")).endswith("Invalidated")
        strength = _number(data, "strength", default=_number(data, "confidence", default=0.5))
        historical = _number(data, "historical_success_rate", default=0.5)
        reliability = _number(data, "reliability", default=0.5)
        volume_confirmation = _number(data, "volume_confirmation", default=0.5)
        trend_confirmation = _number(data, "trend_confirmation", default=0.5)
        volatility = _number(data, "volatility", default=0.5)
        pattern_strength = max(0.0, min(1.0, (strength + historical + reliability + volume_confirmation + trend_confirmation) / 5.0))
        if not validity:
            pattern_strength *= 0.35
        bullish_support = _pattern_support(pattern_text, category, positive=("bull", "up", "continuation", "engulfing", "breakout"), negative=("bear", "down", "breakdown", "reversal"))
        bearish_support = 1.0 - bullish_support
        continuation_support = max(pattern_strength, bullish_support if "continuation" in pattern_text.lower() else 0.0)
        reversal_support = max(1.0 - pattern_strength, bearish_support if "reversal" in pattern_text.lower() or not validity else 0.0)
        breakout_support = max(
            _pattern_support(pattern_text, category, positive=("breakout", "ascending", "bull"), negative=("breakdown", "descending", "bear")),
            0.5 * pattern_strength + 0.25 * volatility,
        )
        breakdown_support = max(
            _pattern_support(pattern_text, category, positive=("breakdown", "descending", "bear"), negative=("breakout", "ascending", "bull")),
            1.0 - pattern_strength,
        )
        evidence.reasoning_factors.update(
            {
                "pattern_strength": pattern_strength,
                "pattern_bullish": bullish_support,
                "pattern_bearish": bearish_support,
                "pattern_continuation": continuation_support,
                "pattern_reversal": reversal_support,
                "pattern_breakout": breakout_support,
                "pattern_breakdown": breakdown_support,
            }
        )
        note = f"pattern={pattern_text or 'unknown'} category={category or 'unknown'}"
        self._apply_supports(
            evidence,
            source_event_type=str(data.get("source_event_type", "PatternDetected")),
            observed_at=evidence.observed_at,
            note=note,
            strength=pattern_strength,
            supports={
                "bullish": bullish_support,
                "bearish": bearish_support,
                "trend_continuation": continuation_support,
                "trend_reversal": reversal_support,
                "breakout": breakout_support,
                "breakdown": breakdown_support,
            },
        )
        self._event_counts[str(data.get("source_event_type", "PatternDetected"))] = (
            self._event_counts.get(str(data.get("source_event_type", "PatternDetected")), 0) + 1
        )
        return self._snapshots_for([key], str(data.get("source_event_type", "PatternDetected")))

    async def observe_indicators(
        self,
        payload: IndicatorsUpdatedPayload | Mapping[str, Any],
    ) -> tuple[ProbabilitySnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = _key_from_payload(data)
        evidence = self._evidence_for_key(key, observed_at=_timestamp(data))
        indicators = dict(data.get("indicators", {}))
        trend_support = _indicator_trend_support(indicators)
        momentum_support = _indicator_momentum_support(indicators)
        volatility_support = _indicator_volatility_support(indicators)
        volume_support = _indicator_volume_support(indicators)
        liquidity_support = _indicator_liquidity_support(indicators)
        structure_support = _indicator_structure_support(indicators)
        evidence.reasoning_factors.update(
            {
                "indicator_trend": trend_support,
                "indicator_momentum": momentum_support,
                "indicator_volatility": volatility_support,
                "indicator_volume": volume_support,
                "indicator_liquidity": liquidity_support,
                "indicator_structure": structure_support,
            }
        )
        note = "indicator update"
        self._apply_supports(
            evidence,
            source_event_type=str(data.get("source_event_type", "IndicatorsUpdated")),
            observed_at=evidence.observed_at,
            note=note,
            strength=max(trend_support, momentum_support, volume_support, liquidity_support),
            supports={
                "bullish": trend_support,
                "bearish": 1.0 - trend_support,
                "trend_continuation": max(trend_support, momentum_support, structure_support),
                "trend_reversal": 1.0 - trend_support,
                "breakout": max(volatility_support, volume_support, momentum_support),
                "breakdown": max(1.0 - trend_support, 1.0 - liquidity_support),
            },
        )
        self._event_counts[str(data.get("source_event_type", "IndicatorsUpdated"))] = (
            self._event_counts.get(str(data.get("source_event_type", "IndicatorsUpdated")), 0) + 1
        )
        return self._snapshots_for([key], str(data.get("source_event_type", "IndicatorsUpdated")))

    async def observe_structure(
        self,
        payload: MarketStructureUpdatedPayload | Mapping[str, Any],
        *,
        source_event_type: str,
    ) -> tuple[ProbabilitySnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = _key_from_payload(data)
        evidence = self._evidence_for_key(key, observed_at=_timestamp(data))
        structure_text = f"{_text(data, 'structure', default='')} {' '.join(str(item) for item in data.get('detected', ())) }".lower()
        detected = tuple(str(item) for item in data.get("detected", ()) if isinstance(item, str))
        confidence = _number(data, "confidence", default=0.5)
        bullish_support = _structure_bullish_support(structure_text, detected, confidence)
        bearish_support = _structure_bearish_support(structure_text, detected, confidence)
        continuation_support = max(bullish_support, _state_support(structure_text, positive=("trend", "bos", "higher high", "higher low"), negative=("change of character", "lower low")))
        reversal_support = max(bearish_support, _state_support(structure_text, positive=("change of character", "breaker", "mitigation"), negative=("higher high", "break of structure")))
        breakout_support = _state_support(structure_text, positive=("breakout", "break of structure", "liquidity sweep", "imbalance"), negative=("breakdown",))
        breakdown_support = _state_support(structure_text, positive=("breakdown", "lower low", "change of character"), negative=("breakout",))
        evidence.reasoning_factors.update(
            {
                "structure_bullish": bullish_support,
                "structure_bearish": bearish_support,
                "structure_continuation": continuation_support,
                "structure_reversal": reversal_support,
                "structure_breakout": breakout_support,
                "structure_breakdown": breakdown_support,
            }
        )
        note = f"structure={_text(data, 'structure', default='unknown')}"
        self._apply_supports(
            evidence,
            source_event_type=source_event_type,
            observed_at=evidence.observed_at,
            note=note,
            strength=max(confidence, bullish_support, breakout_support),
            supports={
                "bullish": bullish_support,
                "bearish": bearish_support,
                "trend_continuation": continuation_support,
                "trend_reversal": reversal_support,
                "breakout": breakout_support,
                "breakdown": breakdown_support,
            },
        )
        self._event_counts[source_event_type] = self._event_counts.get(source_event_type, 0) + 1
        return self._snapshots_for([key], source_event_type)

    async def observe_risk(
        self,
        payload: RiskApprovedPayload | RiskRejectedPayload | PositionSizeCalculatedPayload | StopLossUpdatedPayload | Mapping[str, Any],
        *,
        source_event_type: str,
    ) -> tuple[ProbabilitySnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = _key_from_payload(data)
        evidence = self._evidence_for_key(key, observed_at=_timestamp(data))
        risk_support = _risk_support(data, source_event_type)
        note = f"risk={source_event_type}"
        evidence.reasoning_factors.update(
            {
                "risk": risk_support,
                "risk_approval": risk_support,
                "risk_rejection": 1.0 - risk_support,
            }
        )
        self._apply_supports(
            evidence,
            source_event_type=source_event_type,
            observed_at=evidence.observed_at,
            note=note,
            strength=risk_support,
            supports={
                "bullish": risk_support,
                "bearish": 1.0 - risk_support,
                "trend_continuation": risk_support,
                "trend_reversal": 1.0 - risk_support,
                "breakout": 0.35 + (risk_support * 0.35),
                "breakdown": 1.0 - risk_support,
            },
        )
        self._event_counts[source_event_type] = self._event_counts.get(source_event_type, 0) + 1
        return self._snapshots_for([key], source_event_type)

    async def snapshot_for(
        self,
        *,
        trade_id: str,
        symbol: str,
        exchange: str,
        timeframe: str,
        source_event_type: str,
    ) -> ProbabilitySnapshot:
        key = (trade_id, symbol, exchange, timeframe)
        return self._snapshot_for_key(key, source_event_type)

    async def health_snapshot(self) -> dict[str, int]:
        return {
            "generated": self._generated,
            "tracked_keys": len(self._evidence_by_key),
            **self._event_counts,
        }

    def latest_snapshot(
        self,
        *,
        trade_id: str,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> ProbabilitySnapshot | None:
        return self._snapshot_by_key.get((trade_id, symbol, exchange, timeframe))

    def _snapshots_for(self, keys: list[ProbabilityKey], source_event_type: str) -> tuple[ProbabilitySnapshot, ...]:
        snapshots: list[ProbabilitySnapshot] = []
        for key in keys:
            snapshot = self._snapshot_for_key(key, source_event_type)
            previous = self._snapshot_by_key.get(key)
            if previous is not None:
                probability_delta = max(
                    abs(snapshot.bullish_probability - previous.bullish_probability),
                    abs(snapshot.bearish_probability - previous.bearish_probability),
                    abs(snapshot.trend_continuation_probability - previous.trend_continuation_probability),
                    abs(snapshot.trend_reversal_probability - previous.trend_reversal_probability),
                    abs(snapshot.breakout_probability - previous.breakout_probability),
                    abs(snapshot.breakdown_probability - previous.breakdown_probability),
                )
                confidence_delta = abs(snapshot.confidence - previous.confidence)
                if probability_delta < self._settings.update_delta and confidence_delta < self._settings.confidence_delta:
                    self._snapshot_by_key[key] = snapshot
                    continue
            self._snapshot_by_key[key] = snapshot
            self._generated += 1
            snapshots.append(snapshot)
        return tuple(snapshots)

    def _snapshot_for_key(self, key: ProbabilityKey, source_event_type: str) -> ProbabilitySnapshot:
        evidence = self._evidence_by_key.get(key)
        if evidence is None:
            trade_id, symbol, exchange, timeframe = key
            evidence = ProbabilityEvidence(
                trade_id=trade_id,
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                observed_at=datetime.min.replace(tzinfo=UTC),
            )
            evidence.ensure_priors(self._settings)
        snapshot = self._model.evaluate(evidence, self._settings)
        return ProbabilitySnapshot(
            trade_id=snapshot.trade_id,
            symbol=snapshot.symbol,
            exchange=snapshot.exchange,
            timeframe=snapshot.timeframe,
            observed_at=snapshot.observed_at,
            bullish_probability=snapshot.bullish_probability,
            bearish_probability=snapshot.bearish_probability,
            trend_continuation_probability=snapshot.trend_continuation_probability,
            trend_reversal_probability=snapshot.trend_reversal_probability,
            breakout_probability=snapshot.breakout_probability,
            breakdown_probability=snapshot.breakdown_probability,
            probability_distribution=snapshot.probability_distribution,
            confidence_lower=snapshot.confidence_lower,
            confidence_upper=snapshot.confidence_upper,
            confidence=snapshot.confidence,
            reasoning_metadata=snapshot.reasoning_metadata,
            source_event_type=source_event_type,
            metadata=snapshot.metadata,
        )

    def _evidence_for_key(self, key: ProbabilityKey, *, observed_at: datetime) -> ProbabilityEvidence:
        if key not in self._evidence_by_key:
            trade_id, symbol, exchange, timeframe = key
            evidence = ProbabilityEvidence(
                trade_id=trade_id,
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                observed_at=observed_at.astimezone(UTC),
            )
            evidence.ensure_priors(self._settings)
            self._evidence_by_key[key] = evidence
            while len(self._evidence_by_key) > self._settings.max_tracked_keys:
                expired, _ = self._evidence_by_key.popitem(last=False)
                self._snapshot_by_key.pop(expired, None)
        else:
            evidence = self._evidence_by_key[key]
            self._evidence_by_key.move_to_end(key)
        evidence.observed_at = observed_at.astimezone(UTC)
        return evidence

    def _apply_supports(
        self,
        evidence: ProbabilityEvidence,
        *,
        source_event_type: str,
        observed_at: datetime,
        note: str,
        strength: float,
        supports: Mapping[str, float],
    ) -> None:
        evidence.ensure_priors(self._settings)
        weight = max(0.1, strength) * self._settings.weights.get(source_event_type, 1.0)
        for outcome, support in supports.items():
            posterior = evidence.posteriors[outcome]
            clamped = max(0.0, min(1.0, support))
            posterior.alpha += weight * clamped
            posterior.beta += weight * (1.0 - clamped)
            evidence.reasoning_factors[outcome] = clamped
        evidence.evidence_count += 1
        evidence.touch(source_event_type=source_event_type, observed_at=observed_at, history_limit=self._settings.history_limit)
        evidence.note(note, history_limit=self._settings.history_limit)

    def _context_supports(self, data: Mapping[str, Any]) -> dict[str, float]:
        trend = _component_support(data.get("trend_context"), positive=("bull", "up", "strong"), negative=("bear", "down", "weak"))
        momentum = _component_support(data.get("momentum_context"), positive=("strong", "bull"), negative=("weak", "bear"))
        volatility = _component_support(data.get("volatility_context"), positive=("high",), negative=("low",))
        volume = _component_support(data.get("volume_context"), positive=("expanding", "strong"), negative=("contracting", "weak"))
        liquidity = _component_support(data.get("liquidity_context"), positive=("high",), negative=("low",))
        market = _component_support(data.get("market_context"), positive=("breakout", "bull", "accumulation"), negative=("breakdown", "bear", "distribution"))
        sector = _component_support(data.get("sector_context"), positive=("supportive", "strong"), negative=("weak", "unsupportive"))
        session = _component_support(data.get("session_context"), positive=("opening", "closing", "regular"), negative=("unknown",))
        risk = _component_support(data.get("risk_context"), positive=("low", "moderate"), negative=("high",))
        return {
            "bullish": _avg(trend, momentum, market, sector, session, risk),
            "bearish": _avg(1.0 - trend, 1.0 - market, 1.0 - risk, 1.0 - momentum),
            "trend_continuation": _avg(trend, momentum, liquidity, 1.0 - volatility, risk),
            "trend_reversal": _avg(1.0 - trend, 1.0 - momentum, volatility, 1.0 - risk),
            "breakout": _avg(market, volatility, volume, momentum),
            "breakdown": _avg(1.0 - market, volatility, 1.0 - liquidity, 1.0 - momentum),
        }


def _avg(*values: float) -> float:
    if not values:
        return 0.5
    return max(0.0, min(1.0, sum(values) / len(values)))


def _component_support(component: Any, *, positive: tuple[str, ...], negative: tuple[str, ...]) -> float:
    if not isinstance(component, Mapping):
        return 0.5
    state = _text(component, "state", default="")
    return _state_support(state, positive=positive, negative=negative)


def _state_support(state: str, *, positive: tuple[str, ...], negative: tuple[str, ...]) -> float:
    lowered = state.lower()
    if any(token in lowered for token in positive):
        return 1.0
    if any(token in lowered for token in negative):
        return 0.0
    if "strong" in lowered or "confirmed" in lowered:
        return 0.85
    if "weak" in lowered or "rejected" in lowered:
        return 0.15
    return 0.5


def _market_state_support(state: str) -> float:
    return _state_support(
        state,
        positive=("bull", "breakout", "accumulation", "gap up", "strong momentum"),
        negative=("bear", "breakdown", "distribution", "gap down", "false breakout"),
    )


def _volatility_support(state: str) -> float:
    return _state_support(state, positive=("high", "expanding", "breakout"), negative=("low", "compressed", "quiet"))


def _pattern_support(pattern: str, category: str, *, positive: tuple[str, ...], negative: tuple[str, ...]) -> float:
    lowered = f"{pattern} {category}".lower()
    return _state_support(lowered, positive=positive, negative=negative)


def _indicator_trend_support(indicators: Mapping[str, Any]) -> float:
    score = 0.5
    ema = indicators.get("EMA")
    sma = indicators.get("SMA")
    if isinstance(ema, (int, float)) and isinstance(sma, (int, float)):
        score += 0.15 if float(ema) >= float(sma) else -0.15
    macd = indicators.get("MACD")
    if isinstance(macd, tuple) and len(macd) >= 2:
        score += 0.12 if float(macd[0]) >= float(macd[1]) else -0.12
    rsi = indicators.get("RSI")
    if isinstance(rsi, (int, float)):
        score += 0.08 if float(rsi) >= 55.0 else -0.08 if float(rsi) <= 45.0 else 0.0
    adx = indicators.get("ADX")
    if isinstance(adx, (int, float)):
        score += min(float(adx) / 250.0, 0.08)
    return max(0.0, min(1.0, score))


def _indicator_momentum_support(indicators: Mapping[str, Any]) -> float:
    score = 0.5
    macd = indicators.get("MACD")
    if isinstance(macd, tuple) and len(macd) >= 3:
        score += 0.12 if float(macd[2]) >= 0.0 else -0.12
    rsi = indicators.get("RSI")
    if isinstance(rsi, (int, float)):
        score += 0.08 if float(rsi) >= 60.0 else -0.08 if float(rsi) <= 40.0 else 0.0
    roc = indicators.get("ROC")
    if isinstance(roc, (int, float)):
        score += 0.08 if float(roc) >= 0.0 else -0.08
    return max(0.0, min(1.0, score))


def _indicator_volatility_support(indicators: Mapping[str, Any]) -> float:
    atr = indicators.get("ATR")
    value = 0.5
    if isinstance(atr, (int, float)):
        value = max(0.0, min(1.0, float(atr) / 10.0))
    return value


def _indicator_volume_support(indicators: Mapping[str, Any]) -> float:
    ratio = 1.0
    for key in ("volume_ratio", "relative_volume", "mfi", "obv", "cmf"):
        value = indicators.get(key)
        if isinstance(value, (int, float)):
            ratio = max(ratio, float(value))
    return max(0.0, min(1.0, 0.5 + (ratio - 1.0) * 0.4))


def _indicator_liquidity_support(indicators: Mapping[str, Any]) -> float:
    value = indicators.get("liquidity_score")
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    spread = indicators.get("spread")
    if isinstance(spread, (int, float)):
        return max(0.0, min(1.0, 1.0 - min(1.0, float(spread))))
    return 0.5


def _indicator_structure_support(indicators: Mapping[str, Any]) -> float:
    score = 0.5
    structure = indicators.get("structure")
    if isinstance(structure, str) and structure:
        score += 0.1 if any(token in structure.lower() for token in ("bull", "higher", "breakout")) else -0.1
    return max(0.0, min(1.0, score))


def _structure_bullish_support(structure_text: str, detected: tuple[str, ...], confidence: float) -> float:
    score = _state_support(structure_text, positive=("higher high", "higher low", "break of structure", "bull"), negative=("lower low", "change of character", "bear"))
    if any(item.lower() in {"higher high", "higher low"} for item in detected):
        score = max(score, 0.85)
    return max(0.0, min(1.0, (score * 0.7) + (confidence * 0.3)))


def _structure_bearish_support(structure_text: str, detected: tuple[str, ...], confidence: float) -> float:
    score = _state_support(structure_text, positive=("lower low", "lower high", "change of character", "bear"), negative=("higher high", "bull", "breakout"))
    if any(item.lower() in {"lower low", "lower high", "change of character"} for item in detected):
        score = max(score, 0.85)
    return max(0.0, min(1.0, (score * 0.7) + (confidence * 0.3)))


def _risk_support(data: Mapping[str, Any], source_event_type: str) -> float:
    if source_event_type == str(RISK_APPROVED):
        return 0.88
    if source_event_type == str(RISK_REJECTED):
        return 0.12
    if source_event_type == str(POSITION_SIZE_CALCULATED):
        quantity = _number(data, "quantity", default=1.0)
        return max(0.35, min(0.9, 0.4 + min(quantity / 1000.0, 0.5)))
    if source_event_type == str(STOP_LOSS_UPDATED):
        reason = _text(data, "reason", default="")
        return 0.65 if any(token in reason.lower() for token in ("trail", "protect", "tighten")) else 0.55
    return 0.5


def _key_from_payload(payload: Mapping[str, Any]) -> ProbabilityKey:
    symbol = _text(payload, "symbol", default=GLOBAL)
    exchange = _text(payload, "exchange", default=GLOBAL)
    timeframe = _text(payload, "timeframe", default=GLOBAL)
    trade_id = _text(payload, "trade_id", default="")
    if not trade_id:
        trade_id = f"potential:{exchange}:{symbol}:{timeframe}"
    return trade_id, symbol, exchange, timeframe


def _text(payload: Mapping[str, Any] | Any, *keys: str, default: str) -> str:
    if isinstance(payload, Mapping):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return default


def _number(payload: Mapping[str, Any], key: str, *, default: float) -> float:
    value = payload.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _timestamp(payload: Mapping[str, Any]) -> datetime:
    for key in ("observed_at", "analyzed_at", "occurred_at", "generated_at", "received_at", "timestamp"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    raise ValueError("Probability evidence requires a timezone-aware observed timestamp.")


def _apply_reasoning_metadata(base: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(metadata)
    return merged


__all__ = [
    "BayesianProbabilityModel",
    "ProbabilityEvidence",
    "ProbabilityEngineSettings",
    "ProbabilityModel",
    "ProbabilitySnapshot",
    "ProbabilityKey",
    "ProbabilityEngineService",
]
