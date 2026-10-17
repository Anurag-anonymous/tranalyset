from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED
from tranalyset.strategy_sdk import (
    StrategyMetadata,
    StrategyParameter,
    StrategyPlugin,
)


class MomentumStrategy(StrategyPlugin):
    def __init__(self) -> None:
        metadata = StrategyMetadata(
            strategy_id="momentum_strategy",
            name="Momentum Strategy",
            version="0.1.0",
            description="Entry signals derived from trend, momentum and volume, gated by trade health.",
            parameters=(
                StrategyParameter(
                    name="confidence",
                    parameter_type="float",
                    default_value=0.72,
                    min_value=0.0,
                    max_value=1.0,
                    description="Confidence for generated entry candidates.",
                ),
                StrategyParameter(
                    name="min_volume_ratio",
                    parameter_type="float",
                    default_value=1.1,
                    min_value=0.0,
                    max_value=10.0,
                    description="Minimum volume ratio to consider an entry.",
                ),
                StrategyParameter(
                    name="min_health_score",
                    parameter_type="float",
                    default_value=60.0,
                    min_value=0.0,
                    max_value=100.0,
                    description="Minimum trade health score required to allow entries.",
                ),
            ),
        )
        super().__init__(metadata=metadata)
        self._last_indicators: dict[str, dict[str, float]] = {}
        self._last_trade_health: dict[str, dict[str, Any]] = {}

    @property
    def _subscribes_to_events(self) -> tuple[EventType, ...]:
        return (INDICATORS_UPDATED, TRADE_HEALTH_UPDATED, CANDLE_CLOSED)

    def subscriptions(self) -> dict[EventType, tuple[Any, ...]]:
        return {
            INDICATORS_UPDATED: (self.handle_indicators_updated,),
            TRADE_HEALTH_UPDATED: (self.handle_trade_health_updated,),
            CANDLE_CLOSED: (self.handle_candle_closed,),
        }

    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        indicators = payload.get("indicators", {})
        if indicators:
            self._last_indicators[symbol] = dict(indicators)

    async def handle_trade_health_updated(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        # store latest trade health payload for gating
        self._last_trade_health[symbol] = dict(payload)

    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        timeframe = str(payload.get("timeframe", "1h"))

        indicators = self._last_indicators.get(symbol, {})
        if not indicators:
            return

        trend_score = float(indicators.get("trend_score", 0.0))
        momentum_score = float(indicators.get("momentum_score", 0.0))
        volume_ratio = float(indicators.get("volume_ratio", 1.0))

        # retrieve the latest trade health for the symbol (if any)
        health = self._last_trade_health.get(symbol)
        health_score = float(health.get("health_score", 0.0)) if health else 0.0

        # strategy gating thresholds
        confidence = float(self._configuration.get("confidence", 0.72))
        min_volume = float(self._configuration.get("min_volume_ratio", 1.1))
        min_health = float(self._configuration.get("min_health_score", 60.0))

        # Simple decision rule: require positive trend and momentum, elevated volume, and acceptable health
        if trend_score >= 0.45 and momentum_score >= 0.5 and volume_ratio >= min_volume and health_score >= min_health:
            reasoning = (
                "Trend supports bullish bias",
                "Momentum is positive",
                "Volume is elevated",
                f"Trade health {health_score} meets threshold",
            )
            supporting = {
                "close_price": float(payload.get("close_price", 0.0)),
                "trend_score": trend_score,
                "momentum_score": momentum_score,
                "volume_ratio": volume_ratio,
                "health_score": health_score,
            }
            # reward:risk estimate — conservative default
            await self.publish_candidate(
                symbol=symbol,
                timeframe=timeframe,
                generated_at=event.timestamp,
                confidence=confidence,
                reasoning=reasoning,
                supporting_evidence=supporting,
                reward_risk_estimate=1.8,
                candidate_type="EntryCandidate",
            )
