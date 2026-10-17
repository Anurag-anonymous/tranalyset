from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType, ImmutableValue
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.candlestick.contracts import PATTERN_DETECTED
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED, TICK_RECEIVED
from tranalyset.plugins.market_state_engine.classifier import (
    MarketEvidence,
    MarketStateClassification,
    MarketStateClassifier,
)
from tranalyset.plugins.market_state_engine.contracts import (
    LIQUIDITY_CHANGED,
    MARKET_REGIME_CHANGED,
    MOMENTUM_STATE_CHANGED,
    PUBLISHED_EVENTS,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
    MarketRegimeChangedPayload,
    MomentumStateChangedPayload,
    StateChangePayload,
    VolatilityChangedPayload,
)
from tranalyset.plugins.news.contracts import MARKET_SENTIMENT_CHANGED, NEWS_ANALYZED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED


class MarketStateEnginePlugin(BasePlugin):
    def __init__(self, *, classifier: MarketStateClassifier | None = None) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="market_state_engine",
                name="Market State Engine",
                version="1.0.0",
            )
        )
        self._classifier = classifier or MarketStateClassifier()
        self._evidence_by_key: dict[tuple[str, str], MarketEvidence] = {}
        self._classification_by_key: dict[tuple[str, str], MarketStateClassification] = {}
        self._price_by_symbol: dict[str, float] = {}
        self._volume_by_key: dict[tuple[str, str], float] = {}
        self._event_counts = {
            str(TICK_RECEIVED): 0,
            str(CANDLE_CLOSED): 0,
            str(INDICATORS_UPDATED): 0,
            str(PATTERN_DETECTED): 0,
            str(NEWS_ANALYZED): 0,
            str(MARKET_SENTIMENT_CHANGED): 0,
        }

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Market state engine plugin publishes {event_type}.",
            )

    @subscribes_to(TICK_RECEIVED)
    async def handle_tick_received(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        symbol = _text(event.payload, "symbol", default="UNKNOWN")
        timeframe = "tick"
        price = _number(event.payload, "price", default=self._price_by_symbol.get(symbol, 0.0))
        size = _number(event.payload, "size", default=0.0)
        previous_price = self._price_by_symbol.get(symbol)
        self._price_by_symbol[symbol] = price
        price_change_pct = 0.0
        if previous_price is not None and previous_price > 0:
            price_change_pct = ((price - previous_price) / previous_price) * 100.0
        key = (symbol, timeframe)
        previous_size = self._volume_by_key.get(key, size or 1.0)
        self._volume_by_key[key] = size
        evidence = self._evidence_for(symbol, timeframe, event.timestamp)
        updated = replace(
            evidence,
            observed_at=event.timestamp,
            price_change_pct=price_change_pct,
            volume_ratio=_ratio(size, previous_size),
        )
        await self._classify_and_publish(updated, event.event_type)

    @subscribes_to(CANDLE_CLOSED)
    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        symbol = _text(event.payload, "symbol", default="UNKNOWN")
        timeframe = _text(event.payload, "timeframe", default="candle")
        open_price = _number(event.payload, "open_price", default=0.0)
        high_price = _number(event.payload, "high_price", default=open_price)
        low_price = _number(event.payload, "low_price", default=open_price)
        close_price = _number(event.payload, "close_price", default=open_price)
        volume = _number(event.payload, "volume", default=0.0)
        previous_close = self._price_by_symbol.get(symbol)
        self._price_by_symbol[symbol] = close_price
        key = (symbol, timeframe)
        previous_volume = self._volume_by_key.get(key, volume or 1.0)
        self._volume_by_key[key] = volume
        price_change_pct = 0.0
        if open_price > 0:
            price_change_pct = ((close_price - open_price) / open_price) * 100.0
        gap_pct = 0.0
        if previous_close is not None and previous_close > 0:
            gap_pct = ((open_price - previous_close) / previous_close) * 100.0
        volatility_score = 0.0
        if close_price > 0:
            volatility_score = min(1.0, ((high_price - low_price) / close_price) * 8.0)
        evidence = self._evidence_for(symbol, timeframe, event.timestamp)
        updated = replace(
            evidence,
            observed_at=event.timestamp,
            price_change_pct=price_change_pct,
            gap_pct=gap_pct,
            volume_ratio=_ratio(volume, previous_volume),
            volatility_score=max(evidence.volatility_score, volatility_score),
        )
        await self._classify_and_publish(updated, event.event_type)

    @subscribes_to(INDICATORS_UPDATED)
    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        symbol = _text(event.payload, "symbol", default="UNKNOWN")
        timeframe = _text(event.payload, "timeframe", default="indicator")
        indicators = event.payload.get("indicators", {})
        values = indicators if isinstance(indicators, dict) else dict(indicators)  # type: ignore[arg-type]
        evidence = self._evidence_for(symbol, timeframe, event.timestamp)
        updated = replace(
            evidence,
            observed_at=event.timestamp,
            trend_score=_indicator(values, "trend_score", default=evidence.trend_score),
            momentum_score=_indicator(values, "momentum_score", default=evidence.momentum_score),
            volatility_score=_indicator(
                values,
                "volatility_score",
                default=evidence.volatility_score,
            ),
            liquidity_score=_indicator(values, "liquidity_score", default=evidence.liquidity_score),
            volume_ratio=_indicator(values, "volume_ratio", default=evidence.volume_ratio),
            structure=_text(event.payload, "structure", default=evidence.structure),
            session_phase=_text(event.payload, "session_phase", default=evidence.session_phase),
        )
        await self._classify_and_publish(updated, event.event_type)

    @subscribes_to(PATTERN_DETECTED)
    async def handle_pattern_detected(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        symbol = _text(event.payload, "symbol", default="UNKNOWN")
        timeframe = _text(event.payload, "timeframe", default="pattern")
        evidence = self._evidence_for(symbol, timeframe, event.timestamp)
        updated = replace(
            evidence,
            observed_at=event.timestamp,
            candlestick_pattern=_text(event.payload, "pattern", default=evidence.candlestick_pattern),
            pattern_confidence=_number(event.payload, "confidence", default=evidence.pattern_confidence),
        )
        await self._classify_and_publish(updated, event.event_type)

    @subscribes_to(NEWS_ANALYZED)
    async def handle_news_analyzed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        symbols = _symbols(event.payload)
        for symbol in symbols:
            evidence = self._evidence_for(symbol, "news", event.timestamp)
            confidence = _number(event.payload, "confidence", default=0.5)
            sentiment = _text(event.payload, "sentiment", default=evidence.sentiment)
            updated = replace(
                evidence,
                observed_at=event.timestamp,
                sentiment=sentiment,
                news_sentiment_score=_sentiment_score(sentiment, confidence),
            )
            await self._classify_and_publish(updated, event.event_type)

    @subscribes_to(MARKET_SENTIMENT_CHANGED)
    async def handle_market_sentiment_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        symbol = _text(event.payload, "market", default="GLOBAL")
        confidence = _number(event.payload, "confidence", default=0.5)
        sentiment = _text(event.payload, "sentiment", default="neutral")
        evidence = self._evidence_for(symbol, "sentiment", event.timestamp)
        updated = replace(
            evidence,
            observed_at=event.timestamp,
            sentiment=sentiment,
            news_sentiment_score=_sentiment_score(sentiment, confidence),
        )
        await self._classify_and_publish(updated, event.event_type)

    async def publish_market_regime_changed(self, payload: MarketRegimeChangedPayload) -> None:
        await self.publish(MARKET_REGIME_CHANGED, payload=payload.to_payload())

    async def publish_trend_confirmed(self, payload: StateChangePayload) -> None:
        await self.publish(TREND_CONFIRMED, payload=payload.to_payload())

    async def publish_trend_weakened(self, payload: StateChangePayload) -> None:
        await self.publish(TREND_WEAKENED, payload=payload.to_payload())

    async def publish_volatility_changed(self, payload: VolatilityChangedPayload) -> None:
        await self.publish(VOLATILITY_CHANGED, payload=payload.to_payload())

    async def publish_liquidity_changed(self, payload: StateChangePayload) -> None:
        await self.publish(LIQUIDITY_CHANGED, payload=payload.to_payload())

    async def publish_momentum_state_changed(self, payload: MomentumStateChangedPayload) -> None:
        await self.publish(MOMENTUM_STATE_CHANGED, payload=payload.to_payload())

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if not any(self._event_counts.values()):
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Market state engine is running before market evidence.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Market state engine is classifying market evidence.",
            details={
                **self._event_counts,
                "tracked_markets": len(self._evidence_by_key),
            },
        )

    def current_classification(
        self,
        *,
        symbol: str,
        timeframe: str,
    ) -> MarketStateClassification | None:
        return self._classification_by_key.get((symbol, timeframe))

    async def _classify_and_publish(
        self,
        evidence: MarketEvidence,
        source_event_type: EventType,
    ) -> None:
        key = (evidence.symbol, evidence.timeframe)
        self._evidence_by_key[key] = evidence
        previous = self._classification_by_key.get(key)
        current = self._classifier.classify(evidence)
        self._classification_by_key[key] = current
        await self._publish_changes(evidence, previous, current, source_event_type)

    async def _publish_changes(
        self,
        evidence: MarketEvidence,
        previous: MarketStateClassification | None,
        current: MarketStateClassification,
        source_event_type: EventType,
    ) -> None:
        if previous is None or previous.regime != current.regime:
            await self.publish_market_regime_changed(
                MarketRegimeChangedPayload(
                    symbol=evidence.symbol,
                    timeframe=evidence.timeframe,
                    occurred_at=evidence.observed_at,
                    regime=current.regime,
                    previous_regime=None if previous is None else previous.regime,
                    confidence=current.confidence,
                    evidence=current.evidence,
                    source_event_type=str(source_event_type),
                )
            )

        if previous is None or previous.trend_state != current.trend_state:
            payload = StateChangePayload(
                symbol=evidence.symbol,
                timeframe=evidence.timeframe,
                occurred_at=evidence.observed_at,
                state=current.trend_state,
                previous_state=None if previous is None else previous.trend_state,
                confidence=current.trend_confidence,
                evidence=current.evidence,
                source_event_type=str(source_event_type),
            )
            if current.trend_state.endswith("Confirmed"):
                await self.publish_trend_confirmed(payload)
            elif current.trend_state == "Trend Weakened":
                await self.publish_trend_weakened(payload)

        if previous is None or previous.volatility_state != current.volatility_state:
            await self.publish_volatility_changed(
                VolatilityChangedPayload(
                    symbol=evidence.symbol,
                    timeframe=evidence.timeframe,
                    occurred_at=evidence.observed_at,
                    state=current.volatility_state,
                    previous_state=None if previous is None else previous.volatility_state,
                    confidence=current.volatility_confidence,
                    evidence=current.evidence,
                    source_event_type=str(source_event_type),
                )
            )

        if previous is None or previous.liquidity_state != current.liquidity_state:
            await self.publish_liquidity_changed(
                StateChangePayload(
                    symbol=evidence.symbol,
                    timeframe=evidence.timeframe,
                    occurred_at=evidence.observed_at,
                    state=current.liquidity_state,
                    previous_state=None if previous is None else previous.liquidity_state,
                    confidence=current.liquidity_confidence,
                    evidence=current.evidence,
                    source_event_type=str(source_event_type),
                )
            )

        if previous is None or previous.momentum_state != current.momentum_state:
            await self.publish_momentum_state_changed(
                MomentumStateChangedPayload(
                    symbol=evidence.symbol,
                    timeframe=evidence.timeframe,
                    occurred_at=evidence.observed_at,
                    state=current.momentum_state,
                    previous_state=None if previous is None else previous.momentum_state,
                    confidence=current.momentum_confidence,
                    evidence=current.evidence,
                    source_event_type=str(source_event_type),
                )
            )

    def _evidence_for(self, symbol: str, timeframe: str, observed_at: datetime) -> MarketEvidence:
        return self._evidence_by_key.get(
            (symbol, timeframe),
            MarketEvidence(symbol=symbol, timeframe=timeframe, observed_at=observed_at),
        )

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1


def _number(payload: dict[str, ImmutableValue] | Any, key: str, *, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, int | float):
        return float(value)
    return default


def _indicator(payload: Mapping[str, Any], key: str, *, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, int | float):
        return float(value)
    return default


def _text(payload: dict[str, ImmutableValue] | Any, key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if isinstance(value, str) and value:
        return value
    return default


def _symbols(payload: dict[str, ImmutableValue] | Any) -> tuple[str, ...]:
    symbols = payload.get("symbols", ())
    if isinstance(symbols, tuple):
        parsed = tuple(symbol for symbol in symbols if isinstance(symbol, str) and symbol)
        return parsed or ("GLOBAL",)
    return ("GLOBAL",)


def _ratio(current: float, previous: float) -> float:
    if previous <= 0:
        return 1.0
    return max(0.0, current / previous)


def _sentiment_score(sentiment: str, confidence: float) -> float:
    normalized = sentiment.lower()
    if normalized in {"positive", "bullish", "risk_on"}:
        return confidence
    if normalized in {"negative", "bearish", "risk_off"}:
        return -confidence
    return 0.0


def create_plugin() -> MarketStateEnginePlugin:
    return MarketStateEnginePlugin()
