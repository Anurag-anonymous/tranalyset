from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.plugins.market_data.contracts import CANDLE_CLOSED
from tranalyset.plugins.technical_analysis.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.strategy_sdk import (
    StrategyMetadata,
    StrategyParameter,
    StrategyPlugin,
)


class BreakoutStrategy(StrategyPlugin):
    def __init__(self) -> None:
        metadata = StrategyMetadata(
            strategy_id="breakout_strategy",
            name="Breakout Strategy",
            version="0.1.0",
            description="Detects breakouts, evaluates volume and market regime, emits entry candidates.",
            parameters=(
                StrategyParameter(
                    name="confidence",
                    parameter_type="float",
                    default_value=0.7,
                    min_value=0.0,
                    max_value=1.0,
                    description="Base confidence for breakout signals.",
                ),
                StrategyParameter(
                    name="high_lookback",
                    parameter_type="int",
                    default_value=5,
                    min_value=1,
                    max_value=50,
                    description="Number of previous candles to consider for recent high.",
                ),
                StrategyParameter(
                    name="min_volume_ratio",
                    parameter_type="float",
                    default_value=1.1,
                    min_value=0.0,
                    max_value=10.0,
                    description="Minimum volume ratio for breakout confirmation.",
                ),
                StrategyParameter(
                    name="allowed_regimes",
                    parameter_type="str",
                    default_value="Bull Trend Confirmed,Bull Trend,Trend Developing",
                    description="Comma-separated allowed market regimes for breakouts.",
                ),
            ),
        )
        super().__init__(metadata=metadata)
        # store recent highs per symbol
        self._recent_highs: dict[str, deque[float]] = {}
        # latest indicators per symbol
        self._last_indicators: dict[str, dict[str, float]] = {}
        # latest market regime per symbol
        self._last_regime: dict[str, str] = {}

    @property
    def _subscribes_to_events(self) -> tuple[EventType, ...]:
        return (INDICATORS_UPDATED, MARKET_REGIME_CHANGED, CANDLE_CLOSED)

    def subscriptions(self) -> dict[EventType, tuple[Any, ...]]:
        return {
            INDICATORS_UPDATED: (self.handle_indicators_updated,),
            MARKET_REGIME_CHANGED: (self.handle_market_regime_changed,),
            CANDLE_CLOSED: (self.handle_candle_closed,),
        }

    async def handle_indicators_updated(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        indicators = payload.get("indicators", {})
        if indicators:
            self._last_indicators[symbol] = dict(indicators)

    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        regime = str(payload.get("regime", ""))
        if symbol:
            self._last_regime[symbol] = regime

    async def handle_candle_closed(self, event: EventEnvelope) -> None:
        payload = event.payload
        symbol = str(payload.get("symbol", ""))
        timeframe = str(payload.get("timeframe", "1h"))
        close_price = float(payload.get("close_price", 0.0))
        high_price = float(payload.get("high_price", 0.0))
        volume = float(payload.get("volume", 0.0))

        # update recent highs deque container (lookback size may change in config)
        lookback = int(self._configuration.get("high_lookback", 5))
        highs = self._recent_highs.setdefault(symbol, deque(maxlen=lookback))

        indicators = self._last_indicators.get(symbol, {})
        volume_ratio = float(indicators.get("volume_ratio", 1.0))

        regime = self._last_regime.get(symbol, "")
        allowed_raw = str(self._configuration.get("allowed_regimes", ""))
        allowed = {r.strip() for r in allowed_raw.split(",") if r.strip()}

        min_volume = float(self._configuration.get("min_volume_ratio", 1.1))
        confidence = float(self._configuration.get("confidence", 0.7))

        # breakout detection: only evaluate once we have a full lookback window
        recent_high = None
        if len(highs) >= lookback:
            recent_high = max(list(highs))
        is_breakout = False
        if recent_high is not None and close_price > recent_high * 1.001:
            is_breakout = True

        # after evaluation, record this candle's high for future comparisons
        highs.append(high_price)

        # require volume and regime checks
        volume_ok = volume_ratio >= min_volume or volume >= 1.0
        regime_ok = (not allowed) or any(a.lower() in regime.lower() for a in allowed)

        if is_breakout and volume_ok and regime_ok:
            reasoning = (
                "Price closed above recent highs (breakout)",
                f"volume_ratio={volume_ratio}",
                f"regime={regime}",
            )
            supporting = {
                "close_price": close_price,
                "recent_high": recent_high,
                "volume": volume,
                "volume_ratio": volume_ratio,
                "regime": regime,
            }
            await self.publish_candidate(
                symbol=symbol,
                timeframe=timeframe,
                generated_at=event.timestamp,
                confidence=confidence,
                reasoning=reasoning,
                supporting_evidence=supporting,
                reward_risk_estimate=2.0,
                candidate_type="EntryCandidate",
            )
