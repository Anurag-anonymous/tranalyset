from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tranalyset.plugins.market_state_engine.contracts import SUPPORTED_MARKET_REGIMES


@dataclass(frozen=True, slots=True)
class MarketEvidence:
    symbol: str = "UNKNOWN"
    timeframe: str = "event"
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    price_change_pct: float = 0.0
    gap_pct: float = 0.0
    volume_ratio: float = 1.0
    trend_score: float = 0.0
    momentum_score: float = 0.0
    volatility_score: float = 0.0
    structure: str = "unknown"
    candlestick_pattern: str = "none"
    pattern_confidence: float = 0.0
    news_sentiment_score: float = 0.0
    sentiment: str = "neutral"
    session_phase: str = "continuous"
    liquidity_score: float = 0.5


@dataclass(frozen=True, slots=True)
class MarketStateClassification:
    regime: str
    confidence: float
    trend_state: str
    trend_confidence: float
    volatility_state: str
    volatility_confidence: float
    liquidity_state: str
    liquidity_confidence: float
    momentum_state: str
    momentum_confidence: float
    evidence: Mapping[str, float | str | bool]


class MarketStateClassifier:
    def classify(self, evidence: MarketEvidence) -> MarketStateClassification:
        regime, regime_confidence = self._classify_regime(evidence)
        trend_state, trend_confidence = self._classify_trend(evidence)
        volatility_state, volatility_confidence = self._classify_volatility(evidence)
        liquidity_state, liquidity_confidence = self._classify_liquidity(evidence)
        momentum_state, momentum_confidence = self._classify_momentum(evidence)
        return MarketStateClassification(
            regime=regime,
            confidence=regime_confidence,
            trend_state=trend_state,
            trend_confidence=trend_confidence,
            volatility_state=volatility_state,
            volatility_confidence=volatility_confidence,
            liquidity_state=liquidity_state,
            liquidity_confidence=liquidity_confidence,
            momentum_state=momentum_state,
            momentum_confidence=momentum_confidence,
            evidence=self._evidence_snapshot(evidence),
        )

    def _classify_regime(self, evidence: MarketEvidence) -> tuple[str, float]:
        phase = evidence.session_phase.lower()
        structure = evidence.structure.lower()
        pattern = evidence.candlestick_pattern.lower()
        sentiment = evidence.sentiment.lower()

        if phase == "opening":
            return "Opening Auction", 0.88
        if phase == "closing":
            return "Closing Auction", 0.88
        if evidence.gap_pct >= 1.0:
            return "Gap Up", self._confidence(evidence.gap_pct, threshold=1.0, scale=3.0)
        if evidence.gap_pct <= -1.0:
            return "Gap Down", self._confidence(abs(evidence.gap_pct), threshold=1.0, scale=3.0)
        if "false_breakout" in structure or "false breakout" in pattern:
            return "False Breakout", max(0.7, evidence.pattern_confidence)
        if "false_breakdown" in structure or "false breakdown" in pattern:
            return "False Breakdown", max(0.7, evidence.pattern_confidence)
        if "breakout" in structure and evidence.price_change_pct > 0:
            return "Breakout", self._confidence(evidence.price_change_pct, threshold=1.0, scale=4.0)
        if "breakdown" in structure and evidence.price_change_pct < 0:
            return "Breakdown", self._confidence(abs(evidence.price_change_pct), threshold=1.0, scale=4.0)
        if "exhaustion" in structure or "exhaustion" in pattern:
            return "Trend Exhaustion", max(0.68, evidence.pattern_confidence)
        if evidence.volatility_score >= 0.75:
            return "High Volatility", self._confidence(evidence.volatility_score, threshold=0.75)
        if evidence.volatility_score <= 0.2:
            return "Low Volatility", self._confidence(1.0 - evidence.volatility_score, threshold=0.8)
        if evidence.momentum_score >= 0.72:
            return "Strong Momentum", self._confidence(evidence.momentum_score, threshold=0.72)
        if abs(evidence.momentum_score) <= 0.18:
            return "Weak Momentum", self._confidence(1.0 - abs(evidence.momentum_score), threshold=0.82)
        if "accumulation" in structure or (
            evidence.volume_ratio > 1.3 and sentiment in {"positive", "bullish"}
        ):
            return "Accumulation", 0.72
        if "distribution" in structure or (
            evidence.volume_ratio > 1.3 and sentiment in {"negative", "bearish"}
        ):
            return "Distribution", 0.72
        if evidence.trend_score >= 0.45:
            return "Bull Trend", self._confidence(evidence.trend_score, threshold=0.45)
        if evidence.trend_score <= -0.45:
            return "Bear Trend", self._confidence(abs(evidence.trend_score), threshold=0.45)
        if "range" in structure:
            return "Range Bound", 0.72
        if abs(evidence.price_change_pct) <= 0.2 and abs(evidence.trend_score) < 0.3:
            return "Sideways", 0.68
        return "Range Bound", 0.55

    def _classify_trend(self, evidence: MarketEvidence) -> tuple[str, float]:
        if evidence.trend_score >= 0.55:
            return "Bull Trend Confirmed", self._confidence(evidence.trend_score, threshold=0.55)
        if evidence.trend_score <= -0.55:
            return "Bear Trend Confirmed", self._confidence(abs(evidence.trend_score), threshold=0.55)
        if abs(evidence.trend_score) <= 0.25:
            return "Trend Weakened", self._confidence(1.0 - abs(evidence.trend_score), threshold=0.75)
        return "Trend Developing", 0.58

    def _classify_volatility(self, evidence: MarketEvidence) -> tuple[str, float]:
        if evidence.volatility_score >= 0.7:
            return "High Volatility", self._confidence(evidence.volatility_score, threshold=0.7)
        if evidence.volatility_score <= 0.25:
            return "Low Volatility", self._confidence(1.0 - evidence.volatility_score, threshold=0.75)
        return "Normal Volatility", 0.6

    def _classify_liquidity(self, evidence: MarketEvidence) -> tuple[str, float]:
        combined = (evidence.liquidity_score + min(evidence.volume_ratio / 2.0, 1.0)) / 2.0
        if combined >= 0.7:
            return "High Liquidity", self._confidence(combined, threshold=0.7)
        if combined <= 0.3:
            return "Low Liquidity", self._confidence(1.0 - combined, threshold=0.7)
        return "Normal Liquidity", 0.58

    def _classify_momentum(self, evidence: MarketEvidence) -> tuple[str, float]:
        if evidence.momentum_score >= 0.65:
            return "Strong Momentum", self._confidence(evidence.momentum_score, threshold=0.65)
        if evidence.momentum_score <= -0.65:
            return "Strong Negative Momentum", self._confidence(abs(evidence.momentum_score), threshold=0.65)
        if abs(evidence.momentum_score) <= 0.2:
            return "Weak Momentum", self._confidence(1.0 - abs(evidence.momentum_score), threshold=0.8)
        return "Moderate Momentum", 0.58

    def _evidence_snapshot(self, evidence: MarketEvidence) -> Mapping[str, float | str | bool]:
        return {
            "price_change_pct": evidence.price_change_pct,
            "gap_pct": evidence.gap_pct,
            "volume_ratio": evidence.volume_ratio,
            "trend_score": evidence.trend_score,
            "momentum_score": evidence.momentum_score,
            "volatility_score": evidence.volatility_score,
            "structure": evidence.structure,
            "candlestick_pattern": evidence.candlestick_pattern,
            "pattern_confidence": evidence.pattern_confidence,
            "news_sentiment_score": evidence.news_sentiment_score,
            "sentiment": evidence.sentiment,
            "session_phase": evidence.session_phase,
            "liquidity_score": evidence.liquidity_score,
        }

    def _confidence(self, value: float, *, threshold: float, scale: float = 1.0) -> float:
        adjusted = min(1.0, max(0.0, (value - threshold) / max(scale - threshold, 0.0001)))
        return round(min(0.95, 0.62 + adjusted * 0.33), 4)


def supported_regime_set() -> set[str]:
    return set(SUPPORTED_MARKET_REGIMES)
