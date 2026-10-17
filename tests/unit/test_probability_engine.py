from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.plugins.context_engine.contracts import MARKET_CONTEXT_UPDATED
from tranalyset.plugins.indicator_engine.contracts import INDICATORS_UPDATED
from tranalyset.plugins.market_structure_engine.contracts import MARKET_STRUCTURE_UPDATED
from tranalyset.plugins.pattern_intelligence_engine.contracts import PATTERN_DETECTED
from tranalyset.plugins.probability_engine import (
    CONFIDENCE_UPDATED,
    PROBABILITY_UPDATED,
    ConfidenceUpdatedPayload,
    ProbabilityEnginePlugin,
    ProbabilityEngineService,
    ProbabilitySnapshot,
    ProbabilityUpdatedPayload,
    SUBSCRIBED_EVENTS,
)
from tranalyset.plugins.probability_engine.service import ProbabilityEngineSettings
from tranalyset.plugins.risk.contracts import RISK_APPROVED
from tranalyset.plugins.trade_health_engine.contracts import TRADE_HEALTH_UPDATED


class _CapturingContext:
    def __init__(self, services: Mapping[object, object], *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "probability_engine"
        self.config = config or {}
        self._services = dict(services)
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return self._services[key]


class _InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "probability_engine"
        self.events: list[object] = []
        self.services: dict[object, object] = {}

    def register_event(self, event_type: object, *, description: str, replace: bool = False) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        self.services[key] = instance

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[object], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        self.services[key] = factory


def _now(offset_minutes: int = 0) -> datetime:
    return datetime(2026, 8, 2, 10, 0, tzinfo=UTC) + timedelta(minutes=offset_minutes)


def test_probability_contracts_validate_payloads() -> None:
    payload = ProbabilityUpdatedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=_now(),
        bullish_probability=0.72,
        bearish_probability=0.28,
        trend_continuation_probability=0.81,
        trend_reversal_probability=0.19,
        breakout_probability=0.76,
        breakdown_probability=0.24,
        probability_distribution={
            "bullish": 0.24,
            "bearish": 0.08,
            "trend_continuation": 0.22,
            "trend_reversal": 0.1,
            "breakout": 0.24,
            "breakdown": 0.12,
        },
        confidence_lower=0.61,
        confidence_upper=0.83,
        reasoning_metadata={"model": "BayesianProbabilityModel", "bayesian": True},
        source_event_type=str(MARKET_CONTEXT_UPDATED),
    ).to_payload()

    assert payload["bullish_probability"] == 0.72
    assert payload["confidence_lower"] == 0.61
    assert "trade_action" not in payload
    assert pytest.approx(sum(payload["probability_distribution"].values()), rel=1e-9) == 1.0

    confidence_payload = ConfidenceUpdatedPayload(
        trade_id="trade-1",
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        observed_at=_now(),
        confidence=0.74,
        previous_confidence=0.6,
        confidence_lower=0.61,
        confidence_upper=0.83,
        reasoning_metadata={"evidence_count": 4},
        source_event_type=str(TRADE_HEALTH_UPDATED),
    ).to_payload()

    assert confidence_payload["confidence"] == 0.74
    assert confidence_payload["previous_confidence"] == 0.6
    assert "recommendation" not in confidence_payload

    with pytest.raises(ValueError, match="probability_distribution"):
        ProbabilityUpdatedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            exchange="NASDAQ",
            timeframe="1m",
            observed_at=_now(),
            bullish_probability=0.72,
            bearish_probability=0.28,
            trend_continuation_probability=0.81,
            trend_reversal_probability=0.19,
            breakout_probability=0.76,
            breakdown_probability=0.24,
            probability_distribution={},
            confidence_lower=0.61,
            confidence_upper=0.83,
            reasoning_metadata={},
            source_event_type=str(MARKET_CONTEXT_UPDATED),
        ).to_payload()


def test_probability_plugin_metadata_and_subscriptions() -> None:
    plugin = ProbabilityEnginePlugin()

    assert plugin.metadata.plugin_id == "probability_engine"
    assert plugin.metadata.depends_on == frozenset(
        {
            "context_engine",
            "trade_health_engine",
            "pattern_intelligence_engine",
            "indicator_engine",
            "market_structure_engine",
            "risk",
        }
    )
    assert set(plugin.subscriptions()) == set(SUBSCRIBED_EVENTS)


async def test_probability_service_aggregates_contextual_evidence() -> None:
    service = ProbabilityEngineService(settings=ProbabilityEngineSettings(history_limit=32))
    key = {
        "trade_id": "trade-aapl",
        "symbol": "AAPL",
        "exchange": "NASDAQ",
        "timeframe": "1m",
    }

    await service.observe_market_context(
        {
            **key,
            "observed_at": _now().isoformat(),
            "source_event_type": str(MARKET_CONTEXT_UPDATED),
            "trend_context": {"state": "Bull Trend", "confidence": 0.82, "evidence": {}},
            "momentum_context": {"state": "Strong Momentum", "confidence": 0.79, "evidence": {}},
            "volatility_context": {"state": "High Volatility", "confidence": 0.63, "evidence": {}},
            "volume_context": {"state": "Expanding Volume", "confidence": 0.73, "evidence": {}},
            "liquidity_context": {"state": "High Liquidity", "confidence": 0.71, "evidence": {}},
            "market_context": {"state": "Breakout", "confidence": 0.8, "evidence": {}},
            "sector_context": {"state": "Technology", "confidence": 0.67, "evidence": {}},
            "session_context": {"state": "Regular Session", "confidence": 0.62, "evidence": {}},
            "risk_context": {"state": "Low Risk", "confidence": 0.58, "evidence": {}},
            "context_confidence": 0.81,
            "context_quality": 0.76,
        }
    )
    await service.observe_trade_health(
        {
            **key,
            "occurred_at": _now(1).isoformat(),
            "source_event_type": str(TRADE_HEALTH_UPDATED),
            "health_score": 78.0,
            "confidence_lower": 0.65,
            "confidence_upper": 0.84,
            "factors": {},
        }
    )
    await service.observe_pattern(
        {
            **key,
            "observed_at": _now(2).isoformat(),
            "source_event_type": str(PATTERN_DETECTED),
            "pattern": "Bullish Breakout",
            "category": "continuation",
            "confidence": 0.77,
            "strength": 0.74,
            "age_bars": 3,
            "validity": True,
            "historical_success_rate": 0.71,
            "reliability": 0.69,
            "current_context": {},
            "volume_confirmation": 0.72,
            "trend_confirmation": 0.78,
            "volatility": 0.66,
            "market_regime": "Breakout",
            "evidence": {},
        }
    )
    await service.observe_indicators(
        {
            **key,
            "observed_at": _now(3).isoformat(),
            "source_event_type": str(INDICATORS_UPDATED),
            "indicators": {
                "EMA": 105.0,
                "SMA": 101.2,
                "MACD": (1.2, 0.8, 0.4),
                "RSI": 67.0,
                "ADX": 28.0,
                "ATR": 7.5,
                "volume_ratio": 1.3,
                "liquidity_score": 0.72,
            },
        }
    )
    await service.observe_structure(
        {
            **key,
            "observed_at": _now(4).isoformat(),
            "source_event_type": str(MARKET_STRUCTURE_UPDATED),
            "structure": "Bull Structure",
            "internal_structure": "Higher Highs",
            "external_structure": "External Bull Structure",
            "confidence": 0.83,
            "detected": ["Higher High", "Higher Low", "Break of Structure"],
        },
        source_event_type=str(MARKET_STRUCTURE_UPDATED),
    )
    await service.observe_risk(
        {
            **key,
            "occurred_at": _now(5).isoformat(),
            "source_event_type": str(RISK_APPROVED),
            "reason": "risk approved",
            "evidence": {},
        },
        source_event_type=str(RISK_APPROVED),
    )

    snapshot = await service.snapshot_for(
        trade_id="trade-aapl",
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        source_event_type="ProbabilityProbe",
    )

    assert snapshot.bullish_probability > snapshot.bearish_probability
    assert snapshot.trend_continuation_probability > snapshot.trend_reversal_probability
    assert snapshot.breakout_probability > snapshot.breakdown_probability
    assert pytest.approx(sum(snapshot.probability_distribution.values()), rel=1e-9) == 1.0
    assert snapshot.confidence_lower <= snapshot.confidence <= snapshot.confidence_upper
    assert snapshot.reasoning_metadata["bayesian"] is True


async def test_probability_plugin_publishes_probability_and_confidence_updates() -> None:
    plugin = ProbabilityEnginePlugin()
    install_context = _InstallContext()
    await plugin.install(install_context)

    context = _CapturingContext({})
    await plugin.configure(context)
    await plugin.start(context)

    base_payload = {
        "trade_id": "trade-aapl",
        "symbol": "AAPL",
        "exchange": "NASDAQ",
        "timeframe": "1m",
        "observed_at": _now().isoformat(),
        "source_event_type": str(MARKET_CONTEXT_UPDATED),
        "trend_context": {"state": "Bull Trend", "confidence": 0.84, "evidence": {}},
        "momentum_context": {"state": "Strong Momentum", "confidence": 0.8, "evidence": {}},
        "volatility_context": {"state": "High Volatility", "confidence": 0.64, "evidence": {}},
        "volume_context": {"state": "Expanding Volume", "confidence": 0.74, "evidence": {}},
        "liquidity_context": {"state": "High Liquidity", "confidence": 0.7, "evidence": {}},
        "market_context": {"state": "Breakout", "confidence": 0.8, "evidence": {}},
        "sector_context": {"state": "Technology", "confidence": 0.68, "evidence": {}},
        "session_context": {"state": "Regular Session", "confidence": 0.62, "evidence": {}},
        "risk_context": {"state": "Low Risk", "confidence": 0.58, "evidence": {}},
        "context_confidence": 0.81,
        "context_quality": 0.74,
    }
    await plugin.handle_market_context_updated(
        EventEnvelope.create(
            source="context_engine",
            event_type=MARKET_CONTEXT_UPDATED,
            payload=base_payload,
        )
    )
    await plugin.handle_trade_health_updated(
        EventEnvelope.create(
            source="trade_health_engine",
            event_type=TRADE_HEALTH_UPDATED,
            payload={
                "trade_id": "trade-aapl",
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "occurred_at": _now(1).isoformat(),
                "health_score": 79.0,
                "confidence_lower": 0.66,
                "confidence_upper": 0.85,
                "factors": {},
                "source_event_type": str(TRADE_HEALTH_UPDATED),
            },
        )
    )

    await plugin.stop(context)

    event_types = [event.event_type for event in context.published]
    assert PROBABILITY_UPDATED in event_types
    assert CONFIDENCE_UPDATED in event_types
    assert all(event.source == "probability_engine" for event in context.published)


async def test_probability_plugin_health_transitions() -> None:
    plugin = ProbabilityEnginePlugin()
    install_context = _InstallContext()
    await plugin.install(install_context)

    context = _CapturingContext({})
    await plugin.configure(context)
    await plugin.start(context)

    health = await plugin.health_check()
    assert health.status is HealthStatus.DEGRADED

    await plugin.handle_market_context_updated(
        EventEnvelope.create(
            source="context_engine",
            event_type=MARKET_CONTEXT_UPDATED,
            payload={
                "trade_id": "trade-aapl",
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "timeframe": "1m",
                "observed_at": _now().isoformat(),
                "source_event_type": str(MARKET_CONTEXT_UPDATED),
                "trend_context": {"state": "Bull Trend", "confidence": 0.84, "evidence": {}},
                "momentum_context": {"state": "Strong Momentum", "confidence": 0.8, "evidence": {}},
                "volatility_context": {"state": "High Volatility", "confidence": 0.64, "evidence": {}},
                "volume_context": {"state": "Expanding Volume", "confidence": 0.74, "evidence": {}},
                "liquidity_context": {"state": "High Liquidity", "confidence": 0.7, "evidence": {}},
                "market_context": {"state": "Breakout", "confidence": 0.8, "evidence": {}},
                "sector_context": {"state": "Technology", "confidence": 0.68, "evidence": {}},
                "session_context": {"state": "Regular Session", "confidence": 0.62, "evidence": {}},
                "risk_context": {"state": "Low Risk", "confidence": 0.58, "evidence": {}},
                "context_confidence": 0.81,
                "context_quality": 0.74,
            },
        )
    )

    healthy = await plugin.health_check()
    assert healthy.status is HealthStatus.HEALTHY


class _StubModel:
    def evaluate(self, evidence, settings) -> ProbabilitySnapshot:  # type: ignore[no-untyped-def]
        return ProbabilitySnapshot(
            trade_id=evidence.trade_id,
            symbol=evidence.symbol,
            exchange=evidence.exchange,
            timeframe=evidence.timeframe,
            observed_at=evidence.observed_at,
            bullish_probability=0.9,
            bearish_probability=0.1,
            trend_continuation_probability=0.85,
            trend_reversal_probability=0.15,
            breakout_probability=0.8,
            breakdown_probability=0.2,
            probability_distribution={
                "bullish": 0.3,
                "bearish": 0.1,
                "trend_continuation": 0.25,
                "trend_reversal": 0.1,
                "breakout": 0.2,
                "breakdown": 0.05,
            },
            confidence_lower=0.78,
            confidence_upper=0.94,
            confidence=0.88,
            reasoning_metadata={"model": "StubModel"},
            source_event_type="StubModel",
        )


async def test_probability_service_supports_custom_models() -> None:
    service = ProbabilityEngineService(model=_StubModel())
    await service.observe_market_context(
        {
            "trade_id": "trade-aapl",
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "timeframe": "1m",
            "observed_at": _now().isoformat(),
            "source_event_type": str(MARKET_CONTEXT_UPDATED),
            "trend_context": {"state": "Bull Trend", "confidence": 0.84, "evidence": {}},
            "momentum_context": {"state": "Strong Momentum", "confidence": 0.8, "evidence": {}},
            "volatility_context": {"state": "High Volatility", "confidence": 0.64, "evidence": {}},
            "volume_context": {"state": "Expanding Volume", "confidence": 0.74, "evidence": {}},
            "liquidity_context": {"state": "High Liquidity", "confidence": 0.7, "evidence": {}},
            "market_context": {"state": "Breakout", "confidence": 0.8, "evidence": {}},
            "sector_context": {"state": "Technology", "confidence": 0.68, "evidence": {}},
            "session_context": {"state": "Regular Session", "confidence": 0.62, "evidence": {}},
            "risk_context": {"state": "Low Risk", "confidence": 0.58, "evidence": {}},
            "context_confidence": 0.81,
            "context_quality": 0.74,
        }
    )

    snapshot = await service.snapshot_for(
        trade_id="trade-aapl",
        symbol="AAPL",
        exchange="NASDAQ",
        timeframe="1m",
        source_event_type="Probe",
    )

    assert snapshot.bullish_probability == 0.9
    assert snapshot.reasoning_metadata["model"] == "StubModel"


def test_probability_service_bounds_evidence_and_snapshots_by_recent_activity() -> None:
    service = ProbabilityEngineService(
        settings=ProbabilityEngineSettings(max_tracked_keys=2)
    )
    first = ("trade-1", "AAPL", "NASDAQ", "1m")
    second = ("trade-2", "MSFT", "NASDAQ", "1m")
    third = ("trade-3", "NVDA", "NASDAQ", "1m")

    service._evidence_for_key(first, observed_at=_now())
    service._evidence_for_key(second, observed_at=_now())
    service._evidence_for_key(first, observed_at=_now(1))
    service._evidence_for_key(third, observed_at=_now(2))

    assert tuple(service._evidence_by_key) == (first, third)
    assert len(service._evidence_by_key) == 2
    assert second not in service._snapshot_by_key
