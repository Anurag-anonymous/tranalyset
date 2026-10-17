from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import optional_text_payload, require_text, to_utc_iso

MARKET_REGIME_CHANGED = EventType("MarketRegimeChanged")
TREND_CONFIRMED = EventType("TrendConfirmed")
TREND_WEAKENED = EventType("TrendWeakened")
VOLATILITY_CHANGED = EventType("VolatilityChanged")
LIQUIDITY_CHANGED = EventType("LiquidityChanged")
MOMENTUM_STATE_CHANGED = EventType("MomentumStateChanged")

SUPPORTED_MARKET_REGIMES = (
    "Bull Trend",
    "Bear Trend",
    "Sideways",
    "Range Bound",
    "Breakout",
    "Breakdown",
    "Accumulation",
    "Distribution",
    "High Volatility",
    "Low Volatility",
    "Opening Auction",
    "Closing Auction",
    "Trend Exhaustion",
    "Gap Up",
    "Gap Down",
    "False Breakout",
    "False Breakdown",
    "Strong Momentum",
    "Weak Momentum",
)


def _state_payload(
    *,
    symbol: str,
    timeframe: str,
    occurred_at: datetime,
    confidence: float,
    evidence: Mapping[str, float | str | bool],
    source_event_type: str | None,
) -> dict[str, Any]:
    require_text(symbol, "symbol")
    require_text(timeframe, "timeframe")
    if not 0.0 <= confidence <= 1.0:
        msg = "confidence must be between 0.0 and 1.0."
        raise ValueError(msg)
    payload: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "occurred_at": to_utc_iso(occurred_at, field_name="occurred_at"),
        "confidence": confidence,
        "evidence": dict(evidence),
    }
    optional_text_payload(payload, "source_event_type", source_event_type)
    return payload


@dataclass(frozen=True, slots=True)
class MarketRegimeChangedPayload:
    symbol: str
    timeframe: str
    occurred_at: datetime
    regime: str
    previous_regime: str | None
    confidence: float
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.regime, "regime")
        if self.regime not in SUPPORTED_MARKET_REGIMES:
            msg = f"Unsupported market regime: {self.regime}"
            raise ValueError(msg)
        payload = _state_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            confidence=self.confidence,
            evidence=self.evidence,
            source_event_type=self.source_event_type,
        )
        payload["regime"] = self.regime
        optional_text_payload(payload, "previous_regime", self.previous_regime)
        return payload


@dataclass(frozen=True, slots=True)
class StateChangePayload:
    symbol: str
    timeframe: str
    occurred_at: datetime
    state: str
    previous_state: str | None
    confidence: float
    evidence: Mapping[str, float | str | bool] = field(default_factory=dict)
    source_event_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        require_text(self.state, "state")
        payload = _state_payload(
            symbol=self.symbol,
            timeframe=self.timeframe,
            occurred_at=self.occurred_at,
            confidence=self.confidence,
            evidence=self.evidence,
            source_event_type=self.source_event_type,
        )
        payload["state"] = self.state
        optional_text_payload(payload, "previous_state", self.previous_state)
        return payload


@dataclass(frozen=True, slots=True)
class VolatilityChangedPayload(StateChangePayload):
    pass


@dataclass(frozen=True, slots=True)
class MomentumStateChangedPayload(StateChangePayload):
    pass


PUBLISHED_EVENTS = (
    MARKET_REGIME_CHANGED,
    TREND_CONFIRMED,
    TREND_WEAKENED,
    VOLATILITY_CHANGED,
    LIQUIDITY_CHANGED,
    MOMENTUM_STATE_CHANGED,
)
