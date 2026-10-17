from __future__ import annotations

from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType, ImmutableValue
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec, subscribes_to
from tranalyset.plugins.candlestick.contracts import PATTERN_DETECTED
from tranalyset.plugins.market_state_engine.contracts import (
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
)
from tranalyset.plugins.news.contracts import NEWS_ANALYZED
from tranalyset.plugins.risk.contracts import RISK_APPROVED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.plugins.trade_health_engine.contracts import (
    CONFIDENCE_CHANGED,
    PUBLISHED_EVENTS,
    TRADE_STRENGTH_CHANGED,
    ConfidenceChangedPayload,
    TradeHealthUpdatedPayload,
    TradeStrengthChangedPayload,
)
from tranalyset.plugins.trade_health_engine.scoring import (
    TradeHealthEvidence,
    TradeHealthScorer,
    TradeHealthSnapshot,
)
from tranalyset.plugins.trade_manager.contracts import TRADE_HEALTH_UPDATED


class TradeHealthEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="trade_health_engine",
                name="Trade Health Engine",
                version="1.0.0",
            ),
            configuration_spec=PluginConfigurationSpec(
                (ConfigurationField("weights", dict, required=False, default={}),)
            ),
        )
        self._scorer = TradeHealthScorer()
        self._evidence_by_key: dict[tuple[str, str, str], TradeHealthEvidence] = {}
        self._snapshot_by_key: dict[tuple[str, str, str], TradeHealthSnapshot] = {}
        self._event_counts = {
            str(INDICATORS_UPDATED): 0,
            str(PATTERN_DETECTED): 0,
            str(MARKET_REGIME_CHANGED): 0,
            str(TREND_CONFIRMED): 0,
            str(TREND_WEAKENED): 0,
            str(NEWS_ANALYZED): 0,
            str(RISK_APPROVED): 0,
            str(MOMENTUM_STATE_CHANGED): 0,
        }

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Trade health engine plugin publishes {event_type}.",
            )

    async def on_configure(self) -> None:
        self._scorer = TradeHealthScorer(weights=self.configuration.values.get("weights", {}))

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        indicators = _mapping(event.payload.get("indicators", {}))
        updates = {
            "trend": _signed_score(indicators.get("trend_score")),
            "momentum": _signed_score(indicators.get("momentum_score")),
            "volume": _ratio_score(indicators.get("volume_ratio")),
            "volatility": _volatility_quality(indicators.get("volatility_score")),
            "structure": _unit_score(indicators.get("structure_score")),
            "liquidity": _unit_score(indicators.get("liquidity_score")),
            "sector_strength": _unit_score(indicators.get("sector_strength")),
            "market_strength": _unit_score(indicators.get("market_strength")),
        }
        self._apply_updates(evidence, updates)
        await self._score_and_publish(event)

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        confidence = _number(event.payload.get("confidence"))
        if confidence is not None:
            evidence.update_factor("pattern_quality", confidence * 100.0)
        await self._score_and_publish(event)

    @subscribes_to(MARKET_REGIME_CHANGED)
    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        confidence = _number(event.payload.get("confidence")) or 0.5
        regime = _text(event.payload.get("regime"), default="Range Bound")
        evidence.update_factor("market_strength", _regime_strength(regime, confidence))
        if "volatility" in regime.lower():
            evidence.update_factor("volatility", _volatility_state_score(regime))
        if "range" in regime.lower() or "break" in regime.lower():
            evidence.update_factor("structure", _structure_state_score(regime, confidence))
        await self._score_and_publish(event)

    @subscribes_to(TREND_CONFIRMED)
    async def handle_trend_confirmed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        confidence = _number(event.payload.get("confidence")) or 0.7
        evidence.update_factor("trend", 70.0 + confidence * 25.0)
        await self._score_and_publish(event)

    @subscribes_to(TREND_WEAKENED)
    async def handle_trend_weakened(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        confidence = _number(event.payload.get("confidence")) or 0.7
        evidence.update_factor("trend", 45.0 - confidence * 20.0)
        await self._score_and_publish(event)

    @subscribes_to(NEWS_ANALYZED)
    async def handle_news_analyzed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        sentiment = _text(event.payload.get("sentiment"), default="neutral")
        confidence = _number(event.payload.get("confidence")) or 0.5
        sentiment_score = _sentiment_score(sentiment, confidence)
        evidence.update_factor("news", sentiment_score)
        evidence.update_factor("sentiment", sentiment_score)
        await self._score_and_publish(event)

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        risk_score = _number(event.payload.get("risk_score")) or 90.0
        evidence.update_factor("risk", risk_score)
        await self._score_and_publish(event)

    @subscribes_to(MOMENTUM_STATE_CHANGED)
    async def handle_momentum_state_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        state = _text(event.payload.get("state"), default="Moderate Momentum")
        confidence = _number(event.payload.get("confidence")) or 0.6
        evidence.update_factor("momentum", _momentum_state_score(state, confidence))
        await self._score_and_publish(event)

    async def publish_trade_health_updated(self, payload: TradeHealthUpdatedPayload) -> None:
        await self.publish(TRADE_HEALTH_UPDATED, payload=payload.to_payload())

    async def publish_confidence_changed(self, payload: ConfidenceChangedPayload) -> None:
        await self.publish(CONFIDENCE_CHANGED, payload=payload.to_payload())

    async def publish_trade_strength_changed(self, payload: TradeStrengthChangedPayload) -> None:
        await self.publish(TRADE_STRENGTH_CHANGED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if not any(self._event_counts.values()):
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Trade health engine is running before trade evidence.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Trade health engine is scoring trade evidence.",
            details={
                **self._event_counts,
                "tracked_trades": len(self._evidence_by_key),
            },
        )

    def current_snapshot(
        self,
        *,
        trade_id: str,
        symbol: str,
        timeframe: str,
    ) -> TradeHealthSnapshot | None:
        return self._snapshot_by_key.get((trade_id, symbol, timeframe))

    def _evidence_for(self, event: EventEnvelope) -> TradeHealthEvidence:
        key = _key(event)
        if key not in self._evidence_by_key:
            self._evidence_by_key[key] = TradeHealthEvidence()
        return self._evidence_by_key[key]

    def _apply_updates(self, evidence: TradeHealthEvidence, updates: dict[str, float | None]) -> None:
        for factor, score in updates.items():
            if score is not None:
                evidence.update_factor(factor, score)

    async def _score_and_publish(self, event: EventEnvelope) -> None:
        key = _key(event)
        evidence = self._evidence_by_key[key]
        previous = self._snapshot_by_key.get(key)
        current = self._scorer.score(evidence, previous)
        self._snapshot_by_key[key] = current
        trade_id, symbol, timeframe = key
        if previous is None or abs(current.health_score - previous.health_score) >= 0.0001:
            await self.publish_trade_health_updated(
                TradeHealthUpdatedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    occurred_at=event.timestamp,
                    health_score=current.health_score,
                    previous_health_score=None if previous is None else previous.health_score,
                    confidence_lower=current.confidence_lower,
                    confidence_upper=current.confidence_upper,
                    factors=current.factors,
                    weights=current.weights,
                    explanations=current.explanations,
                    evidence_count=current.evidence_count,
                    source_event_type=str(event.event_type),
                )
            )
        if (
            previous is None
            or current.confidence_lower != previous.confidence_lower
            or current.confidence_upper != previous.confidence_upper
        ):
            await self.publish_confidence_changed(
                ConfidenceChangedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    occurred_at=event.timestamp,
                    confidence_lower=current.confidence_lower,
                    confidence_upper=current.confidence_upper,
                    previous_confidence_lower=None if previous is None else previous.confidence_lower,
                    previous_confidence_upper=None if previous is None else previous.confidence_upper,
                    explanations=current.explanations,
                    source_event_type=str(event.event_type),
                )
            )
        if previous is None or current.strength != previous.strength:
            await self.publish_trade_strength_changed(
                TradeStrengthChangedPayload(
                    trade_id=trade_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    occurred_at=event.timestamp,
                    strength=current.strength,
                    previous_strength=None if previous is None else previous.strength,
                    health_score=current.health_score,
                    explanations=current.explanations,
                    source_event_type=str(event.event_type),
                )
            )

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def _key(event: EventEnvelope) -> tuple[str, str, str]:
    symbol = _text(event.payload.get("symbol"), default="GLOBAL")
    timeframe = _text(event.payload.get("timeframe"), default="event")
    trade_id = _text(event.payload.get("trade_id"), default=f"potential:{symbol}:{timeframe}")
    return trade_id, symbol, timeframe


def _mapping(value: ImmutableValue | object) -> dict[str, Any]:
    if hasattr(value, "items"):
        return dict(value.items())  # type: ignore[union-attr]
    return {}


def _number(value: ImmutableValue | object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _text(value: ImmutableValue | object, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def _unit_score(value: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if -1.0 <= number <= 1.0:
        return (number + 1.0) * 50.0 if number < 0 else number * 100.0
    return number


def _signed_score(value: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return 50.0 + max(-1.0, min(1.0, number)) * 50.0


def _ratio_score(value: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return min(100.0, max(0.0, (number / 2.0) * 100.0))


def _volatility_quality(value: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    normalized = max(0.0, min(1.0, number))
    return max(0.0, 100.0 - abs(normalized - 0.45) * 130.0)


def _regime_strength(regime: str, confidence: float) -> float:
    normalized = regime.lower()
    if normalized in {"bull trend", "breakout", "accumulation", "strong momentum"}:
        return 60.0 + confidence * 35.0
    if normalized in {"bear trend", "breakdown", "distribution", "false breakout", "false breakdown"}:
        return 45.0 - confidence * 20.0
    if normalized in {"sideways", "range bound"}:
        return 55.0
    return 50.0 + (confidence - 0.5) * 20.0


def _structure_state_score(state: str, confidence: float) -> float:
    normalized = state.lower()
    if "false" in normalized:
        return 30.0
    if "breakout" in normalized:
        return 60.0 + confidence * 30.0
    if "breakdown" in normalized:
        return 40.0 - confidence * 20.0
    if "range" in normalized:
        return 55.0
    return 50.0


def _volatility_state_score(state: str) -> float:
    normalized = state.lower()
    if "high" in normalized:
        return 35.0
    if "low" in normalized:
        return 60.0
    return 55.0


def _momentum_state_score(state: str, confidence: float) -> float:
    normalized = state.lower()
    if "strong negative" in normalized:
        return 35.0 - confidence * 10.0
    if "strong" in normalized:
        return 60.0 + confidence * 30.0
    if "weak" in normalized:
        return 45.0 - confidence * 15.0
    return 55.0


def _sentiment_score(sentiment: str, confidence: float) -> float:
    normalized = sentiment.lower()
    if normalized in {"positive", "bullish", "risk_on"}:
        return 50.0 + confidence * 40.0
    if normalized in {"negative", "bearish", "risk_off"}:
        return 50.0 - confidence * 35.0
    return 50.0


def create_plugin() -> TradeHealthEnginePlugin:
    return TradeHealthEnginePlugin()
