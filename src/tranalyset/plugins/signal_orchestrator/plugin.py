from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType, ImmutableValue
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import (
    BasePlugin,
    ConfigurationField,
    PluginConfigurationSpec,
    subscribes_to,
)
from tranalyset.plugins.learning.contracts import STRATEGY_PERFORMANCE_UPDATED
from tranalyset.plugins.market_state_engine.contracts import MARKET_REGIME_CHANGED
from tranalyset.plugins.signal_orchestrator.contracts import (
    ACCEPTED_CANDIDATE,
    CANDIDATE_DECISIONS,
    OrchestratedCandidatePayload,
    REJECTED_CANDIDATE,
    WATCH_CANDIDATE_GENERATED,
)
from tranalyset.plugins.trade_manager.contracts import TRADE_SIGNAL_GENERATED


@dataclass
class CandidateRecord:
    signal_id: str
    strategy_id: str
    candidate_type: str
    symbol: str
    timeframe: str
    generated_at: datetime
    confidence: float
    reasoning: tuple[str, ...]
    supporting_evidence: dict[str, float | str | bool]
    reward_risk_estimate: float
    source_event_type: str
    source_plugin: str
    score: float
    strategy_weight: float
    regime_weight: float
    vote_count: int = 1


class SignalOrchestratorPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="signal_orchestrator",
                name="Signal Orchestrator",
                version="1.0.0",
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("max_candidates", int, required=False, default=10_000),
                    ConfigurationField("max_seen_signal_ids", int, required=False, default=20_000),
                )
            ),
        )
        self._max_candidates = 10_000
        self._max_seen_signal_ids = 20_000
        self._scores: dict[str, float] = {}
        self._regimes: dict[tuple[str, str], str] = {}
        self._candidates: OrderedDict[tuple[str, str, str], CandidateRecord] = OrderedDict()
        self._groups: OrderedDict[tuple[str, str], set[tuple[str, str, str]]] = OrderedDict()
        self._seen_signal_ids: OrderedDict[str, None] = OrderedDict()
        self._event_counts: dict[str, int] = {
            str(TRADE_SIGNAL_GENERATED): 0,
            str(STRATEGY_PERFORMANCE_UPDATED): 0,
            str(MARKET_REGIME_CHANGED): 0,
        }

    async def on_configure(self) -> None:
        self._max_candidates = self.configuration.get("max_candidates", int)
        self._max_seen_signal_ids = self.configuration.get("max_seen_signal_ids", int)
        if self._max_candidates <= 0 or self._max_seen_signal_ids <= 0:
            raise ValueError("Signal orchestrator retention limits must be greater than zero.")

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_event(
            WATCH_CANDIDATE_GENERATED,
            description="Signal orchestrator publishes watch candidate notifications.",
        )
        context.register_event(
            ACCEPTED_CANDIDATE,
            description="Signal orchestrator publishes accepted candidate decisions.",
        )
        context.register_event(
            REJECTED_CANDIDATE,
            description="Signal orchestrator publishes rejected candidate decisions.",
        )

    @subscribes_to(TRADE_SIGNAL_GENERATED)
    async def handle_trade_signal_generated(self, event: EventEnvelope) -> None:
        if event.source == self.metadata.plugin_id:
            return
        self._count(event.event_type)
        candidate = self._parse_candidate(event)
        if candidate is None:
            return
        if candidate.signal_id in self._seen_signal_ids:
            return
        self._seen_signal_ids[candidate.signal_id] = None
        self._seen_signal_ids.move_to_end(candidate.signal_id)
        while len(self._seen_signal_ids) > self._max_seen_signal_ids:
            self._seen_signal_ids.popitem(last=False)
        group_key = (candidate.symbol, candidate.timeframe)
        candidate_key = (candidate.symbol, candidate.timeframe, candidate.candidate_type)
        group = self._groups.setdefault(group_key, set())
        self._groups.move_to_end(group_key)
        group.add(candidate_key)
        existing = self._candidates.get(candidate_key)
        if existing is None:
            self._candidates[candidate_key] = candidate
        else:
            existing.vote_count += 1
            if candidate.score > existing.score or (
                candidate.score == existing.score and candidate.generated_at > existing.generated_at
            ):
                candidate.vote_count = existing.vote_count
                self._candidates[candidate_key] = candidate
            else:
                candidate = existing
        self._candidates.move_to_end(candidate_key)
        while len(self._candidates) > self._max_candidates:
            expired_key, _ = self._candidates.popitem(last=False)
            expired_group_key = expired_key[:2]
            expired_group = self._groups.get(expired_group_key)
            if expired_group is not None:
                expired_group.discard(expired_key)
                if not expired_group:
                    self._groups.pop(expired_group_key, None)
        await self._publish_watch(candidate)
        await self._publish_rankings(group_key)

    @subscribes_to(STRATEGY_PERFORMANCE_UPDATED)
    async def handle_strategy_performance_updated(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        strategy_id = self._text(event.payload.get("strategy_id"), default=event.source)
        self._scores[strategy_id] = self._performance_weight(event.payload)
        while len(self._scores) > self._max_candidates:
            self._scores.pop(next(iter(self._scores)))

    @subscribes_to(MARKET_REGIME_CHANGED)
    async def handle_market_regime_changed(self, event: EventEnvelope) -> None:
        self._count(event.event_type)
        symbol = self._text(event.payload.get("symbol"), default="")
        timeframe = self._text(event.payload.get("timeframe"), default="")
        regime = self._text(event.payload.get("regime"), default="Range Bound")
        if symbol and timeframe:
            self._regimes[(symbol, timeframe)] = regime
            while len(self._regimes) > self._max_candidates:
                self._regimes.pop(next(iter(self._regimes)))

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._event_counts[str(TRADE_SIGNAL_GENERATED)] == 0:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Signal orchestrator is running before candidate events.",
                details=dict(self._event_counts),
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Signal orchestrator is ranking and filtering candidate signals.",
            details={
                **self._event_counts,
                "unique_candidates": len(self._candidates),
                "groups": len(self._groups),
            },
        )

    def _count(self, event_type: EventType) -> None:
        self._event_counts[str(event_type)] += 1

    def _parse_candidate(self, event: EventEnvelope) -> CandidateRecord | None:
        payload = event.payload
        candidate_type = self._text(payload.get("candidate_type"), default="")
        symbol = self._text(payload.get("symbol"), default="")
        timeframe = self._text(payload.get("timeframe"), default="")
        generated_at = self._timestamp(payload.get("generated_at"), default=event.timestamp)
        confidence = self._number(payload.get("confidence"), default=0.0)
        reward_risk_estimate = self._number(payload.get("reward_risk_estimate"), default=0.0)
        reasoning = self._sequence(payload.get("reasoning"))
        supporting_evidence = self._mapping(payload.get("supporting_evidence"))
        if not (candidate_type and symbol and timeframe and reasoning and supporting_evidence):
            return None
        strategy_weight = self._strategy_weight(event.source)
        regime_weight = self._regime_weight(symbol, timeframe)
        score = self._score(confidence, strategy_weight, regime_weight)
        return CandidateRecord(
            signal_id=self._text(payload.get("signal_id"), default=""),
            strategy_id=event.source,
            candidate_type=candidate_type,
            symbol=symbol,
            timeframe=timeframe,
            generated_at=generated_at,
            confidence=confidence,
            reasoning=tuple(reasoning),
            supporting_evidence=supporting_evidence,
            reward_risk_estimate=reward_risk_estimate,
            source_event_type=self._text(payload.get("source_event_type"), default=str(event.event_type)),
            source_plugin=event.source,
            score=score,
            strategy_weight=strategy_weight,
            regime_weight=regime_weight,
        )

    async def _publish_watch(self, candidate: CandidateRecord) -> None:
        if candidate.candidate_type != "WatchCandidate":
            return
        await self.publish(
            WATCH_CANDIDATE_GENERATED,
            payload=OrchestratedCandidatePayload(
                signal_id=candidate.signal_id,
                strategy_id=candidate.strategy_id,
                candidate_type=candidate.candidate_type,
                symbol=candidate.symbol,
                timeframe=candidate.timeframe,
                generated_at=candidate.generated_at,
                confidence=candidate.confidence,
                reasoning=candidate.reasoning,
                supporting_evidence=candidate.supporting_evidence,
                reward_risk_estimate=candidate.reward_risk_estimate,
                source_event_type=candidate.source_event_type,
                decision="watch",
                score=candidate.score,
                rank=1,
                vote_count=candidate.vote_count,
                strategy_weight=candidate.strategy_weight,
                regime_weight=candidate.regime_weight,
                decision_at=candidate.generated_at,
                source_plugin=candidate.source_plugin,
            ).to_payload(),
        )

    async def _publish_rankings(self, group_key: tuple[str, str]) -> None:
        candidate_keys = self._groups.get(group_key, set())
        group_candidates = [self._candidates[key] for key in candidate_keys if key in self._candidates]
        if not group_candidates:
            return
        sorted_candidates = sorted(group_candidates, key=lambda item: item.score, reverse=True)
        for rank, candidate in enumerate(sorted_candidates, start=1):
            decision = "accepted" if rank == 1 else "rejected"
            payload = OrchestratedCandidatePayload(
                signal_id=candidate.signal_id,
                strategy_id=candidate.strategy_id,
                candidate_type=candidate.candidate_type,
                symbol=candidate.symbol,
                timeframe=candidate.timeframe,
                generated_at=candidate.generated_at,
                confidence=candidate.confidence,
                reasoning=candidate.reasoning,
                supporting_evidence=candidate.supporting_evidence,
                reward_risk_estimate=candidate.reward_risk_estimate,
                source_event_type=candidate.source_event_type,
                decision=decision,
                score=candidate.score,
                rank=rank,
                vote_count=candidate.vote_count,
                strategy_weight=candidate.strategy_weight,
                regime_weight=candidate.regime_weight,
                decision_at=candidate.generated_at,
                source_plugin=candidate.source_plugin,
            ).to_payload()
            if decision == "accepted":
                await self.publish(ACCEPTED_CANDIDATE, payload=payload)
            else:
                await self.publish(REJECTED_CANDIDATE, payload=payload)

    def _strategy_weight(self, strategy_id: str) -> float:
        return self._scores.get(strategy_id, 1.0)

    def _regime_weight(self, symbol: str, timeframe: str) -> float:
        regime = self._regimes.get((symbol, timeframe), "Range Bound")
        if "Trend" in regime:
            return 1.05
        if "Range" in regime:
            return 0.95
        return 1.0

    def _score(self, confidence: float, strategy_weight: float, regime_weight: float) -> float:
        return confidence * max(0.0, strategy_weight) * max(0.0, regime_weight)

    def _performance_weight(self, payload: Mapping[str, ImmutableValue]) -> float:
        win_rate = self._number(payload.get("win_rate"), default=0.5)
        reward_risk = self._number(payload.get("average_reward_risk"), default=1.0)
        weight = 1.0 + ((win_rate - 0.5) * 0.5)
        if reward_risk > 0:
            weight += min(reward_risk / 10.0, 0.2)
        return max(0.1, weight)

    def _text(self, value: object | None, *, default: str = "") -> str:
        if isinstance(value, str) and value:
            return value
        return default

    def _number(self, value: object | None, *, default: float = 0.0) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return default

    def _sequence(self, value: object | None) -> tuple[str, ...]:
        if isinstance(value, tuple):
            return tuple(str(item) for item in value if isinstance(item, str) and item)
        if isinstance(value, list):
            return tuple(str(item) for item in value if isinstance(item, str) and item)
        return ()

    def _mapping(self, value: object | None) -> dict[str, float | str | bool]:
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items() if isinstance(item, (int, float, str, bool))}
        return {}

    def _timestamp(self, value: object | None, *, default: datetime) -> datetime:
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
            except ValueError:
                return default
        return default


def create_plugin() -> SignalOrchestratorPlugin:
    return SignalOrchestratorPlugin()


__all__ = [
    "SignalOrchestratorPlugin",
    "create_plugin",
]
