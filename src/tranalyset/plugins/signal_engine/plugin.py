from __future__ import annotations

import time
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType, ImmutableValue
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.candlestick.contracts import PATTERN_DETECTED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.news.contracts import NEWS_ANALYZED
from tranalyset.plugins.risk.contracts import RISK_APPROVED
from tranalyset.plugins.signal_engine.aggregation import SignalCandidateGenerator, SignalEvidence
from tranalyset.plugins.signal_engine.contracts import SignalCandidatePayload
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED
from tranalyset.plugins.trade_manager.contracts import TRADE_SIGNAL_GENERATED

_EVIDENCE_TTL_SECONDS: float = 300.0  # 5 minutes
_EVICTION_CHECK_INTERVAL: int = 50  # evict after this many publishes


class SignalEnginePlugin(BasePlugin):
    def __init__(
        self,
        *,
        generator: SignalCandidateGenerator | None = None,
        evidence_ttl_seconds: float = _EVIDENCE_TTL_SECONDS,
    ) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="signal_engine",
                name="Signal Engine",
                version="1.0.0",
            )
        )
        self._generator = generator or SignalCandidateGenerator()
        self._evidence_by_key: dict[tuple[str, str, str], SignalEvidence] = {}
        self._evidence_timestamps: dict[tuple[str, str, str], float] = {}
        self._evidence_ttl_seconds = evidence_ttl_seconds
        self._publish_counter: int = 0
        self._event_counts = {
            str(TRADE_HEALTH_UPDATED): 0,
            str(MARKET_REGIME_CHANGED): 0,
            str(PATTERN_DETECTED): 0,
            str(INDICATORS_UPDATED): 0,
            str(NEWS_ANALYZED): 0,
            str(RISK_APPROVED): 0,
        }
        self._evictions: int = 0

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_event(
            TRADE_SIGNAL_GENERATED,
            description="Signal engine publishes candidate trade-signal opportunities.",
        )

    @subscribes_to(TRADE_HEALTH_UPDATED)
    async def handle_trade_health_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        evidence.health_score = _number(event.payload.get("health_score"))
        evidence.health_confidence_lower = _number(event.payload.get("confidence_lower"))
        evidence.health_confidence_upper = _number(event.payload.get("confidence_upper"))
        factors = _mapping(event.payload.get("factors", {}))
        evidence.trend_score = _factor_as_unit(factors.get("trend"), evidence.trend_score)
        evidence.momentum_score = _factor_as_unit(factors.get("momentum"), evidence.momentum_score)
        evidence.volume_ratio = _number(factors.get("volume")) or evidence.volume_ratio
        await self._publish_candidates(evidence, event.event_type)

    @subscribes_to(MARKET_REGIME_CHANGED)
    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        evidence.regime = _text(event.payload.get("regime"), default=evidence.regime or "Range Bound")
        evidence.reward_risk_estimate = _number(event.payload.get("reward_risk_estimate")) or evidence.reward_risk_estimate
        await self._publish_candidates(evidence, event.event_type)

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        evidence.pattern = _text(event.payload.get("pattern"), default=evidence.pattern or "unknown")
        evidence.pattern_confidence = _number(event.payload.get("confidence")) or evidence.pattern_confidence
        await self._publish_candidates(evidence, event.event_type)

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        indicators = _mapping(event.payload.get("indicators", {}))
        evidence.trend_score = _number(indicators.get("trend_score")) or evidence.trend_score
        evidence.momentum_score = _number(indicators.get("momentum_score")) or evidence.momentum_score
        evidence.volume_ratio = _number(indicators.get("volume_ratio")) or evidence.volume_ratio
        evidence.reward_risk_estimate = (
            _number(indicators.get("reward_risk_estimate")) or evidence.reward_risk_estimate
        )
        await self._publish_candidates(evidence, event.event_type)

    @subscribes_to(NEWS_ANALYZED)
    async def handle_news_analyzed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        for key in _keys_for_news_event(event):
            evidence = self._evidence_for_key(key, event)
            evidence.news_sentiment = _text(event.payload.get("sentiment"), default="neutral")
            evidence.news_confidence = _number(event.payload.get("confidence")) or evidence.news_confidence
            await self._publish_candidates(evidence, event.event_type)

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        evidence = self._evidence_for(event)
        evidence.risk_approved = True
        evidence.reward_risk_estimate = (
            _number(event.payload.get("reward_risk_estimate")) or evidence.reward_risk_estimate
        )
        await self._publish_candidates(evidence, event.event_type)

    async def publish_trade_signal_generated(self, payload: SignalCandidatePayload) -> None:
        await self.publish(TRADE_SIGNAL_GENERATED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if not any(self._event_counts.values()):
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Signal engine is running before evidence.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Signal engine is aggregating evidence into candidates.",
            details={
                **self._event_counts,
                "tracked_opportunities": len(self._evidence_by_key),
            },
        )

    def current_evidence(
        self,
        *,
        trade_id: str,
        symbol: str,
        timeframe: str,
    ) -> SignalEvidence | None:
        return self._evidence_by_key.get((trade_id, symbol, timeframe))

    async def _publish_candidates(self, evidence: SignalEvidence, source_event_type: EventType) -> None:
        evidence.source_events.add(str(source_event_type))
        evidence.observed_at = evidence.observed_at
        self._publish_counter += 1
        if self._publish_counter % _EVICTION_CHECK_INTERVAL == 0:
            self._evict_stale_evidence()
        for candidate in self._generator.generate(evidence, source_event_type=str(source_event_type)):
            await self.publish_trade_signal_generated(candidate)

    def _evidence_for(self, event: EventEnvelope) -> SignalEvidence:
        return self._evidence_for_key(_key(event), event)

    def _evidence_for_key(
        self,
        key: tuple[str, str, str],
        event: EventEnvelope,
    ) -> SignalEvidence:
        now = time.monotonic()
        if key not in self._evidence_by_key:
            self._evidence_timestamps[key] = now
            trade_id, symbol, timeframe = key
            self._evidence_by_key[key] = SignalEvidence(
                trade_id=None if trade_id.startswith("potential:") else trade_id,
                symbol=symbol,
                timeframe=timeframe,
                observed_at=event.timestamp,
            )
        else:
            self._evidence_timestamps[key] = now
        evidence = self._evidence_by_key[key]
        evidence.observed_at = event.timestamp
        return evidence

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1

    def _evict_stale_evidence(self) -> None:
        now = time.monotonic()
        stale_keys = [
            key
            for key, ts in self._evidence_timestamps.items()
            if now - ts >= self._evidence_ttl_seconds
        ]
        for key in stale_keys:
            self._evidence_by_key.pop(key, None)
            self._evidence_timestamps.pop(key, None)
        if stale_keys:
            self._evictions += len(stale_keys)


def _key(event: EventEnvelope) -> tuple[str, str, str]:
    symbol = _text(event.payload.get("symbol"), default="GLOBAL")
    timeframe = _text(event.payload.get("timeframe"), default="event")
    trade_id = _text(event.payload.get("trade_id"), default=f"potential:{symbol}:{timeframe}")
    return trade_id, symbol, timeframe


def _keys_for_news_event(event: EventEnvelope) -> tuple[tuple[str, str, str], ...]:
    trade_id = _text(event.payload.get("trade_id"), default="")
    timeframe = _text(event.payload.get("timeframe"), default="event")
    if trade_id:
        symbol = _text(event.payload.get("symbol"), default="GLOBAL")
        return ((trade_id, symbol, timeframe),)
    symbols = event.payload.get("symbols", ())
    if isinstance(symbols, tuple) and symbols:
        return tuple((f"potential:{symbol}:{timeframe}", str(symbol), timeframe) for symbol in symbols if isinstance(symbol, str))
    symbol = _text(event.payload.get("symbol"), default="GLOBAL")
    return ((f"potential:{symbol}:{timeframe}", symbol, timeframe),)


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


def _factor_as_unit(value: object, default: float | None) -> float | None:
    number = _number(value)
    if number is None:
        return default
    if number > 1.0:
        return (number - 50.0) / 50.0
    return number


def create_plugin() -> SignalEnginePlugin:
    return SignalEnginePlugin()
