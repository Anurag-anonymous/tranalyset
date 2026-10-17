from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.strategy_sdk import (
    StrategyMetadata,
    StrategyParameter,
    StrategyPlugin,
)


class MeanReversionStrategy(StrategyPlugin):
    def __init__(self) -> None:
        metadata = StrategyMetadata(
            strategy_id="mean_reversion_strategy",
            name="Mean Reversion Strategy",
            version="0.1.0",
            description="Entry signals when RSI and volatility indicate mean reversion in a range-bound regime.",
            parameters=(
                StrategyParameter(
                    name="confidence",
                    parameter_type="float",
                    default_value=0.65,
                    min_value=0.0,
                    max_value=1.0,
                    description="Base confidence for mean reversion signals.",
                ),
                StrategyParameter(
                    name="rsi_oversold",
                    parameter_type="float",
                    default_value=30.0,
                    min_value=0.0,
                    max_value=100.0,
                    description="RSI threshold for oversold condition (entry trigger for long).",
                ),
                StrategyParameter(
                    name="atr_multiplier",
                    parameter_type="float",
                    default_value=1.0,
                    min_value=0.0,
                    max_value=10.0,
                    description="Multiplier for ATR to define range bounds.",
                ),
                StrategyParameter(
                    name="min_volatility",
                    parameter_type="float",
                    default_value=0.5,
                    min_value=0.0,
                    max_value=100.0,
                    description="Minimum volatility threshold to allow mean reversion.",
                ),
            ),
        )
        super().__init__(metadata=metadata)
        self._last_indicators: dict[str, dict[str, float]] = {}
        self._last_candle: dict[str, dict[str, float]] = {}

    @property
    def _subscribes_to_events(self) -> tuple[EventType, ...]:
        return (INDICATORS_UPDATED, CANDLE_CLOSED)

    def subscriptions(self) -> dict[EventType, tuple[Any, ...]]:
        return {
            INDICATORS_UPDATED: (self.handle_indicators_updated,),
            CANDLE_CLOSED: (self.handle_candle_closed,),
        }

    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        indicators = payload.get("indicators", {})
        if indicators:
            self._last_indicators[symbol] = dict(indicators)

    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        timeframe = str(payload.get("timeframe", "1h"))
        close_price = float(payload.get("close_price", 0.0))
        high_price = float(payload.get("high_price", 0.0))
        low_price = float(payload.get("low_price", 0.0))

        # store candle for range calculation
        self._last_candle[symbol] = {
            "close": close_price,
            "high": high_price,
            "low": low_price,
        }

        indicators = self._last_indicators.get(symbol, {})
        if not indicators:
            return

        rsi = float(indicators.get("rsi", 50.0))
        atr = float(indicators.get("atr", 0.0))
        volatility = float(indicators.get("volatility", 0.0))

        # strategy thresholds
        confidence = float(self._configuration.get("confidence", 0.65))
        rsi_oversold = float(self._configuration.get("rsi_oversold", 30.0))
        atr_mult = float(self._configuration.get("atr_multiplier", 1.0))
        min_vol = float(self._configuration.get("min_volatility", 0.5))

        # Mean reversion logic:
        # - RSI oversold condition (< rsi_oversold)
        # - ATR-based range: range must exist for mean reversion opportunity
        # - Volatility must support mean reversion (between min_vol and a max threshold)
        # - Range-bound check: price should be near low relative to ATR range

        range_size = atr * atr_mult if atr > 0 else (high_price - low_price)
        if range_size <= 0:
            return

        is_oversold = rsi < rsi_oversold
        vol_ok = min_vol <= volatility <= 20.0  # allow mean reversion in moderate volatility
        price_near_low = (close_price - low_price) <= (range_size * 0.5)

        if is_oversold and vol_ok and price_near_low:
            reasoning = (
                f"RSI={rsi} indicates oversold condition",
                f"Volatility={volatility} supports mean reversion",
                f"Price near low of range (atr_range={range_size})",
            )
            supporting = {
                "rsi": rsi,
                "atr": atr,
                "volatility": volatility,
                "range_size": range_size,
                "close_price": close_price,
                "high_price": high_price,
                "low_price": low_price,
            }
            await self.publish_candidate(
                symbol=symbol,
                timeframe=timeframe,
                generated_at=event.timestamp,
                confidence=confidence,
                reasoning=reasoning,
                supporting_evidence=supporting,
                reward_risk_estimate=1.5,
                candidate_type="EntryCandidate",
            )
