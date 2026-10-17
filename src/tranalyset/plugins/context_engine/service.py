from __future__ import annotations

import statistics
from collections import OrderedDict, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tranalyset.plugins.feature_engine.contracts import FeatureSet
from tranalyset.plugins.indicator_engine.contracts import IndicatorsUpdatedPayload
from tranalyset.plugins.market_state_engine.contracts import (
    MarketRegimeChangedPayload,
    StateChangePayload,
)
from tranalyset.plugins.market_structure_engine.contracts import MarketStructureUpdatedPayload
from tranalyset.plugins.news.contracts import MarketSentimentChangedPayload, NewsAnalyzedPayload
from tranalyset.plugins.pattern_intelligence_engine.contracts import (
    PatternDetectedPayload,
    PatternInvalidatedPayload,
    PatternUpdatedPayload,
)

ContextKey = tuple[str, str, str]
GLOBAL = "GLOBAL"


@dataclass(frozen=True, slots=True)
class ContextComponent:
    state: str
    confidence: float
    evidence: Mapping[str, float | str | bool]
    observed_at: datetime
    source_event_type: str

    def __post_init__(self) -> None:
        if not self.state:
            msg = "state must not be empty."
            raise ValueError(msg)
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        if self.observed_at.tzinfo is None:
            msg = "observed_at must be timezone-aware."
            raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "source_event_type": self.source_event_type,
        }


@dataclass(frozen=True, slots=True)
class MarketContextSnapshot:
    symbol: str
    exchange: str
    timeframe: str
    observed_at: datetime
    trend_context: ContextComponent
    momentum_context: ContextComponent
    volatility_context: ContextComponent
    volume_context: ContextComponent
    liquidity_context: ContextComponent
    market_context: ContextComponent
    sector_context: ContextComponent
    session_context: ContextComponent
    risk_context: ContextComponent
    context_confidence: float
    context_quality: float
    evidence: Mapping[str, float | str | bool]
    source_event_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.symbol:
            msg = "symbol must not be empty."
            raise ValueError(msg)
        if not self.exchange:
            msg = "exchange must not be empty."
            raise ValueError(msg)
        if self.observed_at.tzinfo is None:
            msg = "observed_at must be timezone-aware."
            raise ValueError(msg)
        if not 0.0 <= self.context_confidence <= 1.0:
            msg = "context_confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        if not 0.0 <= self.context_quality <= 1.0:
            msg = "context_quality must be between 0.0 and 1.0."
            raise ValueError(msg)

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "timeframe": self.timeframe,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "trend_context": self.trend_context.to_payload(),
            "momentum_context": self.momentum_context.to_payload(),
            "volatility_context": self.volatility_context.to_payload(),
            "volume_context": self.volume_context.to_payload(),
            "liquidity_context": self.liquidity_context.to_payload(),
            "market_context": self.market_context.to_payload(),
            "sector_context": self.sector_context.to_payload(),
            "session_context": self.session_context.to_payload(),
            "risk_context": self.risk_context.to_payload(),
            "context_confidence": self.context_confidence,
            "context_quality": self.context_quality,
            "evidence": dict(self.evidence),
            "source_event_type": self.source_event_type,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ContextEngineSettings:
    history_limit: int = 180
    min_confidence: float = 0.5
    update_delta: float = 0.05
    max_tracked_keys: int = 5_000

    def __post_init__(self) -> None:
        if self.history_limit <= 0:
            msg = "history_limit must be greater than zero."
            raise ValueError(msg)
        if self.max_tracked_keys <= 0:
            msg = "max_tracked_keys must be greater than zero."
            raise ValueError(msg)
        if not 0.0 <= self.min_confidence <= 1.0:
            msg = "min_confidence must be between 0.0 and 1.0."
            raise ValueError(msg)
        if self.update_delta < 0.0:
            msg = "update_delta must not be negative."
            raise ValueError(msg)


@dataclass(slots=True)
class _ComponentBook:
    trend: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    momentum: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    volatility: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    volume: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    liquidity: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    market: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    sector: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    session: dict[ContextKey, ContextComponent] = field(default_factory=dict)
    risk: dict[ContextKey, ContextComponent] = field(default_factory=dict)


class ContextEngineService:
    def __init__(self, *, settings: ContextEngineSettings | None = None) -> None:
        self._settings = settings or ContextEngineSettings()
        self._books = _ComponentBook()
        self._known_keys: OrderedDict[ContextKey, None] = OrderedDict()
        self._known_keys_by_symbol: dict[str, set[ContextKey]] = defaultdict(set)
        self._last_snapshot: dict[ContextKey, MarketContextSnapshot] = {}
        self._event_counts = {name: 0 for name in (
            "indicator",
            "feature",
            "pattern",
            "structure",
            "news",
            "market_state",
        )}
        self._generated = 0

    @property
    def generated(self) -> int:
        return self._generated

    async def observe_indicators(self, payload: IndicatorsUpdatedPayload | Mapping[str, Any]) -> tuple[MarketContextSnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = self._key(
            str(data.get("symbol", "")),
            str(data.get("exchange", GLOBAL)),
            str(data.get("timeframe", GLOBAL)),
        )
        self._register_key(key)
        indicators = dict(data.get("indicators", {}))
        observed_at = _parse_timestamp(data.get("observed_at"))
        source = str(data.get("source_event_type", "IndicatorsUpdated"))
        trend = self._trend_from_indicators(indicators, observed_at, source)
        momentum = self._momentum_from_indicators(indicators, observed_at, source)
        if key not in self._books.trend or trend.confidence >= self._books.trend[key].confidence:
            self._books.trend[key] = trend
        if key not in self._books.momentum or momentum.confidence >= self._books.momentum[key].confidence:
            self._books.momentum[key] = momentum
        self._books.volatility[key] = self._volatility_from_indicators(indicators, observed_at, source)
        self._books.volume[key] = self._volume_from_indicators(indicators, observed_at, source)
        self._books.liquidity[key] = self._liquidity_from_indicators(indicators, observed_at, source)
        self._books.risk[key] = self._risk_from_components(key, observed_at, source)
        self._event_counts["indicator"] += 1
        return self._snapshots_for([key], source)

    async def observe_features(self, feature_set: FeatureSet | Mapping[str, Any]) -> tuple[MarketContextSnapshot, ...]:
        data = dict(feature_set.to_payload() if hasattr(feature_set, "to_payload") else feature_set)
        key = self._key(
            str(data.get("symbol", "")),
            str(data.get("exchange", GLOBAL)),
            str(data.get("timeframe", GLOBAL)),
        )
        self._register_key(key)
        features = dict(data.get("features", {}))
        observed_at = _parse_timestamp(data.get("observed_at"))
        source = str(data.get("source_event_type", "FeaturesUpdated"))
        trend = self._trend_from_features(features, observed_at, source)
        momentum = self._momentum_from_features(features, observed_at, source)
        if key not in self._books.trend or trend.confidence >= self._books.trend[key].confidence:
            self._books.trend[key] = trend
        if key not in self._books.momentum or momentum.confidence >= self._books.momentum[key].confidence:
            self._books.momentum[key] = momentum
        self._books.volume[key] = self._volume_from_features(features, observed_at, source)
        self._books.liquidity[key] = self._liquidity_from_features(features, observed_at, source)
        self._books.session[key] = self._session_from_features(features, observed_at, source)
        metadata = data.get("metadata", {})
        if isinstance(metadata, Mapping):
            sector = next(
                (
                    str(metadata[key_name])
                    for key_name in ("sector", "industry", "theme")
                    if isinstance(metadata.get(key_name), str) and metadata.get(key_name)
                ),
                "",
            )
            if sector:
                self._books.sector[key] = self._component(
                    sector,
                    0.6,
                    {"sector": sector},
                    observed_at,
                    source,
                )
        self._books.risk[key] = self._risk_from_components(key, observed_at, source)
        self._event_counts["feature"] += 1
        return self._snapshots_for([key], source)

    async def observe_pattern(
        self,
        payload: PatternDetectedPayload | PatternUpdatedPayload | PatternInvalidatedPayload | Mapping[str, Any],
    ) -> tuple[MarketContextSnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = self._key(
            str(data.get("symbol", "")),
            str(data.get("exchange", GLOBAL)),
            str(data.get("timeframe", GLOBAL)),
        )
        self._register_key(key)
        observed_at = _parse_timestamp(data.get("observed_at"))
        source = str(data.get("source_event_type", "PatternDetected"))
        source_is_invalidated = str(data.get("source_event_type", "")).endswith("Invalidated")
        valid = not source_is_invalidated and data.get("validity", True) is not False
        pattern_name = str(data.get("pattern", "Pattern"))
        if not valid:
            pattern_name = f"{pattern_name} Invalidated"
        confidence = float(data.get("confidence", 0.0) or 0.0) if valid else max(0.0, float(data.get("confidence", 0.0) or 0.0) * 0.35)
        evidence = {
            "pattern": data.get("pattern", ""),
            "category": data.get("category", ""),
            "valid": valid,
            "reason": data.get("reason", ""),
        }
        self._books.trend[key] = self._component(self._pattern_trend_state(pattern_name), confidence, evidence, observed_at, source)
        self._books.market[key] = self._component(self._pattern_market_state(pattern_name), confidence, evidence, observed_at, source)
        self._books.risk[key] = self._risk_from_components(key, observed_at, source, pattern_confidence=confidence)
        self._event_counts["pattern"] += 1
        return self._snapshots_for([key], source)

    async def observe_structure(self, payload: MarketStructureUpdatedPayload | Mapping[str, Any], *, source_event_type: str) -> tuple[MarketContextSnapshot, ...]:
        data = dict(payload.to_payload() if hasattr(payload, "to_payload") else payload)
        key = self._key(
            str(data.get("symbol", "")),
            str(data.get("exchange", GLOBAL)),
            str(data.get("timeframe", GLOBAL)),
        )
        self._register_key(key)
        observed_at = _parse_timestamp(data.get("observed_at"))
        detected = tuple(str(item) for item in data.get("detected", ()))
        trend_state = self._structure_trend_state(data.get("structure", ""), detected)
        market_state = self._structure_market_state(data.get("structure", ""), detected)
        liquidity_state = self._structure_liquidity_state(detected)
        confidence = float(data.get("confidence", 0.0) or 0.0)
        evidence = {
            "structure": data.get("structure", ""),
            "internal_structure": data.get("internal_structure", ""),
            "external_structure": data.get("external_structure", ""),
            "detected": list(detected),
            "break_of_structure": bool(data.get("break_of_structure", False)),
            "character_change": bool(data.get("character_change", False)),
            "premium_zone": bool(data.get("premium_zone", False)),
            "discount_zone": bool(data.get("discount_zone", False)),
        }
        self._books.trend[key] = self._component(trend_state, confidence, evidence, observed_at, source_event_type)
        self._books.market[key] = self._component(market_state, confidence, evidence, observed_at, source_event_type)
        self._books.liquidity[key] = self._component(liquidity_state, confidence, evidence, observed_at, source_event_type)
        self._books.risk[key] = self._risk_from_components(key, observed_at, source_event_type)
        self._event_counts["structure"] += 1
        return self._snapshots_for([key], source_event_type)

    async def observe_market_state(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        state: str,
        confidence: float,
        evidence: Mapping[str, float | str | bool],
        observed_at: datetime,
        source_event_type: str,
    ) -> tuple[MarketContextSnapshot, ...]:
        key = self._key(symbol, exchange, timeframe)
        self._register_key(key)
        trend_state = self._market_trend_state(state)
        momentum_state = self._market_momentum_state(state)
        volatility_state = self._market_volatility_state(state)
        liquidity_state = self._market_liquidity_state(state)
        session_state = self._market_session_state(state)
        market_state = state
        if trend_state != "Sideways" or key not in self._books.trend:
            self._books.trend[key] = self._component(trend_state, confidence, evidence, observed_at, source_event_type)
        if momentum_state != "Neutral Momentum" or key not in self._books.momentum:
            self._books.momentum[key] = self._component(momentum_state, confidence, evidence, observed_at, source_event_type)
        if volatility_state != "Normal Volatility" or key not in self._books.volatility:
            self._books.volatility[key] = self._component(volatility_state, confidence, evidence, observed_at, source_event_type)
        if liquidity_state != "Normal Liquidity" or key not in self._books.liquidity:
            self._books.liquidity[key] = self._component(liquidity_state, confidence, evidence, observed_at, source_event_type)
        self._books.market[key] = self._component(market_state, confidence, evidence, observed_at, source_event_type)
        self._books.session[key] = self._component(session_state, confidence, evidence, observed_at, source_event_type)
        self._books.risk[key] = self._risk_from_components(key, observed_at, source_event_type)
        self._event_counts["market_state"] += 1
        return self._snapshots_for([key], source_event_type)

    async def observe_news(
        self,
        *,
        symbols: tuple[str, ...],
        sentiment: str,
        confidence: float,
        evidence: Mapping[str, float | str | bool],
        observed_at: datetime,
        source_event_type: str,
        market: str | None = None,
    ) -> tuple[MarketContextSnapshot, ...]:
        target_keys: list[ContextKey] = []
        for symbol in symbols:
            symbol_key = self._key(symbol, GLOBAL, GLOBAL)
            self._register_key(symbol_key)
            self._books.market[symbol_key] = self._component(self._news_market_state(sentiment), confidence, evidence, observed_at, source_event_type)
            self._books.sector[symbol_key] = self._component(self._sector_state(evidence, sentiment), confidence, evidence, observed_at, source_event_type)
            self._books.risk[symbol_key] = self._risk_from_components(symbol_key, observed_at, source_event_type, news_sentiment=sentiment)
            target_keys.extend(self._known_keys_by_symbol.get(symbol, {symbol_key}))

        if market:
            market_key = self._key(GLOBAL, market, GLOBAL)
            self._register_key(market_key)
            self._books.market[market_key] = self._component(self._news_market_state(sentiment), confidence, evidence, observed_at, source_event_type)
            self._books.sector[market_key] = self._component(self._sector_state(evidence, sentiment), confidence, evidence, observed_at, source_event_type)
            self._books.risk[market_key] = self._risk_from_components(market_key, observed_at, source_event_type, news_sentiment=sentiment)
            target_keys.append(market_key)

        if not target_keys:
            global_key = self._key(GLOBAL, GLOBAL, GLOBAL)
            self._register_key(global_key)
            self._books.market[global_key] = self._component(self._news_market_state(sentiment), confidence, evidence, observed_at, source_event_type)
            self._books.sector[global_key] = self._component(self._sector_state(evidence, sentiment), confidence, evidence, observed_at, source_event_type)
            self._books.risk[global_key] = self._risk_from_components(global_key, observed_at, source_event_type, news_sentiment=sentiment)
            target_keys.append(global_key)

        self._event_counts["news"] += 1
        return self._snapshots_for(target_keys, source_event_type)

    async def snapshot_for(self, *, symbol: str, exchange: str, timeframe: str, source_event_type: str) -> MarketContextSnapshot:
        key = self._key(symbol, exchange, timeframe)
        return self._snapshot_for_key(key, source_event_type)

    async def health_snapshot(self) -> dict[str, int]:
        return {
            "generated": self._generated,
            "tracked_keys": len(self._known_keys),
            **self._event_counts,
        }

    def latest_snapshot(self, *, symbol: str, exchange: str, timeframe: str) -> MarketContextSnapshot | None:
        return self._last_snapshot.get(self._key(symbol, exchange, timeframe))

    def _snapshots_for(self, keys: list[ContextKey], source_event_type: str) -> tuple[MarketContextSnapshot, ...]:
        snapshots: list[MarketContextSnapshot] = []
        for key in keys:
            snapshot = self._snapshot_for_key(key, source_event_type)
            previous = self._last_snapshot.get(key)
            if previous is not None:
                delta = abs(snapshot.context_confidence - previous.context_confidence)
                quality_delta = abs(snapshot.context_quality - previous.context_quality)
                if delta < self._settings.update_delta and quality_delta < self._settings.update_delta:
                    self._last_snapshot[key] = snapshot
                    continue
            self._last_snapshot[key] = snapshot
            self._generated += 1
            snapshots.append(snapshot)
        return tuple(snapshots)

    def _snapshot_for_key(self, key: ContextKey, source_event_type: str) -> MarketContextSnapshot:
        trend = self._select_component(key, self._books.trend, default_state="Sideways")
        momentum = self._select_component(key, self._books.momentum, default_state="Neutral")
        volatility = self._select_component(key, self._books.volatility, default_state="Normal Volatility")
        volume = self._select_component(key, self._books.volume, default_state="Neutral Volume")
        liquidity = self._select_component(key, self._books.liquidity, default_state="Normal Liquidity")
        market = self._select_component(key, self._books.market, default_state="Range Bound")
        sector = self._select_component(key, self._books.sector, default_state="Unknown Sector")
        session = self._select_component(key, self._books.session, default_state="Unknown Session")
        risk = self._select_component(key, self._books.risk, default_state="Moderate Risk")
        observed_at = max(
            component.observed_at
            for component in (trend, momentum, volatility, volume, liquidity, market, sector, session, risk)
        )
        evidence = {
            "sources": [
                component.source_event_type
                for component in (trend, momentum, volatility, volume, liquidity, market, sector, session, risk)
                if component.source_event_type
            ],
            "tracked_components": self._tracked_count(key),
            "trend_state": trend.state,
            "momentum_state": momentum.state,
            "volatility_state": volatility.state,
            "volume_state": volume.state,
            "liquidity_state": liquidity.state,
            "market_state": market.state,
            "sector_state": sector.state,
            "session_state": session.state,
            "risk_state": risk.state,
        }
        confidence = self._context_confidence(trend, momentum, volatility, volume, liquidity, market, sector, session, risk)
        quality = self._context_quality(
            trend,
            momentum,
            volatility,
            volume,
            liquidity,
            market,
            sector,
            session,
            risk,
            reference_time=observed_at,
        )
        symbol, exchange, timeframe = key
        return MarketContextSnapshot(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            observed_at=observed_at,
            trend_context=trend,
            momentum_context=momentum,
            volatility_context=volatility,
            volume_context=volume,
            liquidity_context=liquidity,
            market_context=market,
            sector_context=sector,
            session_context=session,
            risk_context=risk,
            context_confidence=confidence,
            context_quality=quality,
            evidence=evidence,
            source_event_type=source_event_type,
        )

    def _select_component(
        self,
        key: ContextKey,
        book: dict[ContextKey, ContextComponent],
        *,
        default_state: str,
    ) -> ContextComponent:
        for candidate in self._candidate_keys(key):
            component = book.get(candidate)
            if component is not None:
                return component
        return self._component(default_state, 0.0, {}, datetime.min.replace(tzinfo=UTC), "context_engine")

    def _candidate_keys(self, key: ContextKey) -> tuple[ContextKey, ...]:
        symbol, exchange, timeframe = key
        return (
            (symbol, exchange, timeframe),
            (symbol, GLOBAL, GLOBAL),
            (GLOBAL, exchange, GLOBAL),
            (GLOBAL, GLOBAL, GLOBAL),
        )

    def _register_key(self, key: ContextKey) -> None:
        if key in self._known_keys:
            self._known_keys.move_to_end(key)
        else:
            self._known_keys[key] = None
        self._known_keys_by_symbol[key[0]].add(key)
        while len(self._known_keys) > self._settings.max_tracked_keys:
            expired, _ = self._known_keys.popitem(last=False)
            self._last_snapshot.pop(expired, None)
            for book in (
                self._books.trend,
                self._books.momentum,
                self._books.volatility,
                self._books.volume,
                self._books.liquidity,
                self._books.market,
                self._books.sector,
                self._books.session,
                self._books.risk,
            ):
                book.pop(expired, None)
            symbol_keys = self._known_keys_by_symbol.get(expired[0])
            if symbol_keys is not None:
                symbol_keys.discard(expired)
                if not symbol_keys:
                    self._known_keys_by_symbol.pop(expired[0], None)

    def _tracked_count(self, key: ContextKey) -> int:
        return sum(1 for candidate in self._candidate_keys(key) if candidate in self._known_keys)

    def _component(
        self,
        state: str,
        confidence: float,
        evidence: Mapping[str, float | str | bool],
        observed_at: datetime,
        source_event_type: str,
    ) -> ContextComponent:
        return ContextComponent(
            state=state,
            confidence=max(0.0, min(1.0, confidence)),
            evidence=dict(evidence),
            observed_at=observed_at.astimezone(UTC),
            source_event_type=source_event_type,
        )

    def _context_confidence(self, *components: ContextComponent) -> float:
        weights = {
            "trend": 1.2,
            "momentum": 1.0,
            "volatility": 0.9,
            "volume": 0.8,
            "liquidity": 0.9,
            "market": 1.1,
            "sector": 0.7,
            "session": 0.6,
            "risk": 0.8,
        }
        weighted = 0.0
        total_weight = 0.0
        observed = 0
        for name, component in zip(weights, components, strict=False):
            if component.confidence <= 0.0:
                continue
            weight = weights[name]
            weighted += component.confidence * weight
            total_weight += weight
            observed += 1
        base = weighted / total_weight if total_weight else 0.0
        coverage = observed / len(components)
        consistency = self._consistency(components)
        return max(0.0, min(1.0, base * (0.7 + (coverage * 0.3)) * consistency))

    def _context_quality(
        self,
        *components: ContextComponent,
        reference_time: datetime,
    ) -> float:
        coverage = sum(1 for component in components if component.confidence > 0.0) / len(components)
        consistency = self._consistency(components)
        recency = self._recency_score(components, reference_time=reference_time)
        return max(0.0, min(1.0, (coverage * 0.45) + (consistency * 0.35) + (recency * 0.2)))

    def _recency_score(
        self,
        components: tuple[ContextComponent, ...],
        *,
        reference_time: datetime,
    ) -> float:
        latest = max(component.observed_at for component in components)
        age_minutes = max(0.0, (reference_time.astimezone(UTC) - latest).total_seconds() / 60.0)
        return max(0.0, min(1.0, 1.0 - (age_minutes / 240.0)))

    def _consistency(self, components: tuple[ContextComponent, ...]) -> float:
        trend = self._direction_score(components[0].state)
        momentum = self._direction_score(components[1].state)
        market = self._direction_score(components[5].state)
        volatility = 1.0 if "High" in components[2].state else 0.0 if "Low" in components[2].state else 0.5
        liquidity = 1.0 if "High" in components[4].state else 0.0 if "Low" in components[4].state else 0.5
        score = 1.0
        score -= abs(trend - momentum) * 0.15
        score -= abs(trend - market) * 0.15
        score -= abs(volatility - (1.0 - liquidity)) * 0.1
        return max(0.55, min(1.0, score))

    def _direction_score(self, state: str) -> float:
        lowered = state.lower()
        if "bull" in lowered or "up" in lowered or "strong" in lowered:
            return 1.0
        if "bear" in lowered or "down" in lowered:
            return -1.0
        if "weak" in lowered:
            return 0.25
        return 0.0

    def _trend_from_indicators(
        self,
        indicators: Mapping[str, Any],
        observed_at: datetime,
        source_event_type: str,
    ) -> ContextComponent:
        score = 0.5
        if isinstance(indicators.get("EMA"), (int, float)) and isinstance(indicators.get("SMA"), (int, float)):
            score += 0.12 if float(indicators["EMA"]) >= float(indicators["SMA"]) else -0.12
        macd = indicators.get("MACD")
        if isinstance(macd, tuple) and len(macd) >= 2:
            score += 0.15 if macd[0] >= macd[1] else -0.15
        rsi = indicators.get("RSI")
        if isinstance(rsi, (int, float)):
            score += 0.08 if float(rsi) >= 55.0 else -0.08 if float(rsi) <= 45.0 else 0.0
        adx = indicators.get("ADX")
        if isinstance(adx, (int, float)):
            score += min(float(adx) / 200.0, 0.1)
        state = "Bull Trend" if score >= 0.62 else "Bear Trend" if score <= 0.38 else "Sideways"
        confidence = min(1.0, 0.55 + abs(score - 0.5) * 1.5)
        return self._component(state, confidence, indicators, observed_at, source_event_type)

    def _momentum_from_indicators(
        self,
        indicators: Mapping[str, Any],
        observed_at: datetime,
        source_event_type: str,
    ) -> ContextComponent:
        score = 0.5
        macd = indicators.get("MACD")
        if isinstance(macd, tuple) and len(macd) >= 3:
            score += 0.15 if macd[2] >= 0 else -0.15
        rsi = indicators.get("RSI")
        if isinstance(rsi, (int, float)):
            score += 0.08 if float(rsi) >= 60.0 else -0.08 if float(rsi) <= 40.0 else 0.0
        state = "Strong Momentum" if score >= 0.62 else "Weak Momentum" if score <= 0.38 else "Neutral Momentum"
        confidence = min(1.0, 0.5 + abs(score - 0.5) * 1.6)
        return self._component(state, confidence, indicators, observed_at, source_event_type)

    def _volatility_from_indicators(
        self,
        indicators: Mapping[str, Any],
        observed_at: datetime,
        source_event_type: str,
    ) -> ContextComponent:
        atr = indicators.get("ATR")
        value = 0.5
        if isinstance(atr, (int, float)):
            value = min(1.0, max(0.0, float(atr) / 10.0))
        state = "High Volatility" if value >= 0.65 else "Low Volatility" if value <= 0.35 else "Normal Volatility"
        confidence = min(1.0, 0.55 + abs(value - 0.5) * 1.3)
        return self._component(state, confidence, indicators, observed_at, source_event_type)

    def _volume_from_indicators(
        self,
        indicators: Mapping[str, Any],
        observed_at: datetime,
        source_event_type: str,
    ) -> ContextComponent:
        ratio = 1.0
        for key in ("relative_volume", "volume_ratio"):
            value = indicators.get(key)
            if isinstance(value, (int, float)):
                ratio = max(ratio, float(value))
        if ratio >= 1.25:
            state = "Expanding Volume"
        elif ratio <= 0.8:
            state = "Contracting Volume"
        else:
            state = "Neutral Volume"
        confidence = min(1.0, 0.5 + abs(ratio - 1.0) * 0.5)
        return self._component(state, confidence, indicators, observed_at, source_event_type)

    def _liquidity_from_indicators(
        self,
        indicators: Mapping[str, Any],
        observed_at: datetime,
        source_event_type: str,
    ) -> ContextComponent:
        value = 0.5
        for key in ("cmf", "liquidity", "liquidity_score"):
            candidate = indicators.get(key)
            if isinstance(candidate, (int, float)):
                value = max(value, min(1.0, max(0.0, float(candidate))))
        state = "High Liquidity" if value >= 0.65 else "Low Liquidity" if value <= 0.35 else "Normal Liquidity"
        confidence = min(1.0, 0.5 + abs(value - 0.5) * 1.5)
        return self._component(state, confidence, indicators, observed_at, source_event_type)

    def _trend_from_features(self, features: Mapping[str, Any], observed_at: datetime, source_event_type: str) -> ContextComponent:
        score = 0.5
        for key in ("price_change", "log_returns", "percentage_returns", "ema_distance", "sma_distance", "trend_window"):
            value = features.get(key)
            if isinstance(value, (int, float)):
                score += 0.08 if float(value) > 0 else -0.08 if float(value) < 0 else 0.0
        state = "Bull Trend" if score >= 0.62 else "Bear Trend" if score <= 0.38 else "Sideways"
        confidence = min(1.0, 0.45 + abs(score - 0.5) * 1.6)
        return self._component(state, confidence, features, observed_at, source_event_type)

    def _momentum_from_features(self, features: Mapping[str, Any], observed_at: datetime, source_event_type: str) -> ContextComponent:
        score = 0.5
        for key in ("momentum_window", "price_change", "log_returns", "percentage_returns"):
            value = features.get(key)
            if isinstance(value, (int, float)):
                score += 0.1 if float(value) > 0 else -0.1 if float(value) < 0 else 0.0
        state = "Strong Momentum" if score >= 0.62 else "Weak Momentum" if score <= 0.38 else "Neutral Momentum"
        confidence = min(1.0, 0.45 + abs(score - 0.5) * 1.7)
        return self._component(state, confidence, features, observed_at, source_event_type)

    def _volume_from_features(self, features: Mapping[str, Any], observed_at: datetime, source_event_type: str) -> ContextComponent:
        ratio = 1.0
        for key in ("relative_volume", "volume_ratio", "volume_delta"):
            value = features.get(key)
            if isinstance(value, (int, float)):
                ratio = max(ratio, float(value))
        if ratio >= 1.25:
            state = "Expanding Volume"
        elif ratio <= 0.8:
            state = "Contracting Volume"
        else:
            state = "Neutral Volume"
        confidence = min(1.0, 0.45 + abs(ratio - 1.0) * 0.55)
        return self._component(state, confidence, features, observed_at, source_event_type)

    def _liquidity_from_features(self, features: Mapping[str, Any], observed_at: datetime, source_event_type: str) -> ContextComponent:
        value = 0.5
        for key in ("liquidity_metrics", "liquidity_score", "spread", "bid_ask_spread"):
            candidate = features.get(key)
            if isinstance(candidate, (int, float)):
                value = min(1.0, max(0.0, float(candidate)))
        state = "High Liquidity" if value >= 0.65 else "Low Liquidity" if value <= 0.35 else "Normal Liquidity"
        confidence = min(1.0, 0.45 + abs(value - 0.5) * 1.4)
        return self._component(state, confidence, features, observed_at, source_event_type)

    def _session_from_features(self, features: Mapping[str, Any], observed_at: datetime, source_event_type: str) -> ContextComponent:
        session = "Unknown Session"
        for key in ("session_phase", "session", "market_session"):
            value = features.get(key)
            if isinstance(value, str) and value:
                session = value
                break
        for key in ("session_position", "time_since_open", "time_until_close"):
            value = features.get(key)
            if isinstance(value, (int, float)) and not session.startswith("Unknown"):
                break
        confidence = 0.6 if session != "Unknown Session" else 0.2
        return self._component(session, confidence, features, observed_at, source_event_type)

    def _risk_from_components(
        self,
        key: ContextKey,
        observed_at: datetime,
        source_event_type: str,
        *,
        pattern_confidence: float | None = None,
        news_sentiment: str | None = None,
    ) -> ContextComponent:
        trend = self._select_component(key, self._books.trend, default_state="Sideways")
        volatility = self._select_component(key, self._books.volatility, default_state="Normal Volatility")
        liquidity = self._select_component(key, self._books.liquidity, default_state="Normal Liquidity")
        market = self._select_component(key, self._books.market, default_state="Range Bound")
        risk_score = 0.35
        if "High" in volatility.state:
            risk_score += 0.22
        if "Low" in liquidity.state:
            risk_score += 0.2
        if "Bear" in trend.state or "Breakdown" in market.state:
            risk_score += 0.1
        if pattern_confidence is not None:
            risk_score += max(0.0, 0.08 - pattern_confidence * 0.05)
        if news_sentiment is not None and news_sentiment.lower() in {"negative", "bearish", "risk_off"}:
            risk_score += 0.12
        state = "High Risk" if risk_score >= 0.7 else "Low Risk" if risk_score <= 0.42 else "Moderate Risk"
        confidence = min(1.0, 0.45 + abs(risk_score - 0.5) * 1.2)
        evidence = {
            "trend": trend.state,
            "volatility": volatility.state,
            "liquidity": liquidity.state,
            "market": market.state,
            "risk_score": round(risk_score, 4),
        }
        return self._component(state, confidence, evidence, observed_at, source_event_type)

    def _structure_trend_state(self, structure: str, detected: tuple[str, ...]) -> str:
        text = f"{structure} {' '.join(detected)}".lower()
        if "bull" in text or "higher high" in text or "break of structure" in text:
            return "Bull Trend"
        if "bear" in text or "lower low" in text or "change of character" in text:
            return "Bear Trend"
        return "Sideways"

    def _structure_market_state(self, structure: str, detected: tuple[str, ...]) -> str:
        text = f"{structure} {' '.join(detected)}".lower()
        if "breakout" in text:
            return "Breakout"
        if "breakdown" in text:
            return "Breakdown"
        if "accumulation" in text:
            return "Accumulation"
        if "distribution" in text:
            return "Distribution"
        if "range" in text:
            return "Range Bound"
        return "Range Bound"

    def _structure_liquidity_state(self, detected: tuple[str, ...]) -> str:
        if "Liquidity Sweep" in detected or "Liquidity Grab" in detected:
            return "Low Liquidity"
        return "Normal Liquidity"

    def _pattern_trend_state(self, pattern: str) -> str:
        lowered = pattern.lower()
        if "bull" in lowered or "breakout" in lowered or "continuation" in lowered:
            return "Bull Trend"
        if "bear" in lowered or "breakdown" in lowered or "reversal" in lowered:
            return "Bear Trend"
        return "Sideways"

    def _pattern_market_state(self, pattern: str) -> str:
        lowered = pattern.lower()
        if "breakout" in lowered:
            return "Breakout"
        if "breakdown" in lowered:
            return "Breakdown"
        if "liquidity" in lowered:
            return "Liquidity Event"
        return "Range Bound"

    def _market_trend_state(self, state: str) -> str:
        lowered = state.lower()
        if "bull" in lowered or "confirmed" in lowered:
            return "Bull Trend"
        if "bear" in lowered or "weakened" in lowered:
            return "Bear Trend"
        return "Sideways"

    def _market_momentum_state(self, state: str) -> str:
        lowered = state.lower()
        if any(token in lowered for token in ("strong", "bull", "breakout", "confirmed", "accumulation")):
            return "Strong Momentum"
        if any(token in lowered for token in ("weak", "weakened", "bear", "breakdown", "exhaustion", "distribution")):
            return "Weak Momentum"
        return "Neutral Momentum"

    def _market_volatility_state(self, state: str) -> str:
        lowered = state.lower()
        if "high" in lowered:
            return "High Volatility"
        if "low" in lowered:
            return "Low Volatility"
        return "Normal Volatility"

    def _market_liquidity_state(self, state: str) -> str:
        lowered = state.lower()
        if "high" in lowered:
            return "High Liquidity"
        if "low" in lowered:
            return "Low Liquidity"
        return "Normal Liquidity"

    def _market_session_state(self, state: str) -> str:
        lowered = state.lower()
        if "opening" in lowered:
            return "Opening Session"
        if "closing" in lowered:
            return "Closing Session"
        return "Regular Session"

    def _news_market_state(self, sentiment: str) -> str:
        lowered = sentiment.lower()
        if lowered in {"positive", "bullish", "risk_on"}:
            return "Bull Trend"
        if lowered in {"negative", "bearish", "risk_off"}:
            return "Bear Trend"
        return "Range Bound"

    def _sector_state(self, evidence: Mapping[str, float | str | bool], sentiment: str) -> str:
        for key in ("sector", "industry", "theme"):
            value = evidence.get(key)
            if isinstance(value, str) and value:
                return value
        if sentiment.lower() in {"positive", "bullish"}:
            return "Sector Supportive"
        if sentiment.lower() in {"negative", "bearish"}:
            return "Sector Weak"
        return "Unknown Sector"

    def _key(self, symbol: str, exchange: str, timeframe: str) -> ContextKey:
        return (symbol or GLOBAL, exchange or GLOBAL, timeframe or GLOBAL)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    raise ValueError("Context evidence requires a timezone-aware observed timestamp.")


__all__ = [
    "ContextComponent",
    "ContextEngineService",
    "ContextEngineSettings",
    "ContextKey",
    "GLOBAL",
    "MarketContextSnapshot",
]
