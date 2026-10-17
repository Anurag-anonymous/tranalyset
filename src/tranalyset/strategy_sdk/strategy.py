from __future__ import annotations

import inspect
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from tranalyset.contracts.events import EventType
from tranalyset.contracts.plugins import (
    HealthStatus,
    PluginContext,
    PluginHealth,
    PluginInstallContext,
    PluginMetadata,
)
from tranalyset.plugin_sdk import BasePlugin
from tranalyset.plugin_sdk.configuration import ConfigurationField, PluginConfigurationSpec
from tranalyset.plugins.signal_engine.contracts import (
    CANDIDATE_TYPES,
    TradeSignalCandidate,
    EntryCandidate,
    ExitCandidate,
    WatchCandidate,
    PartialExitCandidate,
    TrailCandidate,
)
from tranalyset.plugins.trade_manager.contracts import TRADE_SIGNAL_GENERATED
from tranalyset.strategy_sdk.contracts import StrategyMetadata, StrategyParameter

_CANDIDATE_CLASS_MAP: dict[str, type[Any]] = {
    "TradeSignalCandidate": TradeSignalCandidate,
    "EntryCandidate": EntryCandidate,
    "ExitCandidate": ExitCandidate,
    "WatchCandidate": WatchCandidate,
    "PartialExitCandidate": PartialExitCandidate,
    "TrailCandidate": TrailCandidate,
}


class StrategyPlugin(BasePlugin):
    def __init__(
        self,
        *,
        metadata: StrategyMetadata,
    ) -> None:
        plugin_metadata = PluginMetadata(
            plugin_id=metadata.strategy_id,
            name=metadata.name,
            version=metadata.version,
        )

        # Build configuration spec from declared parameters so incoming configs validate
        config_fields: list[ConfigurationField] = []
        for p in metadata.parameters:
            type_map = {"int": int, "float": float, "bool": bool, "str": str}
            value_type = type_map.get(p.parameter_type, str)
            config_fields.append(
                ConfigurationField(
                    name=p.name,
                    value_type=value_type,
                    required=False,
                    default=p.default_value,
                )
            )

        super().__init__(metadata=plugin_metadata, configuration_spec=PluginConfigurationSpec(tuple(config_fields)))
        self._strategy_metadata = metadata
        self._configuration: dict[str, Any] = {}
        self._candidates_generated = 0
        self._performance_stats: dict[str, Any] = {}
        self._candidate_types_count: dict[str, int] = defaultdict(int)

    async def on_install(self, context: PluginInstallContext) -> None:
        if not context.is_event_registered(TRADE_SIGNAL_GENERATED):
            context.register_event(
                TRADE_SIGNAL_GENERATED,
                description="Strategy plugins publish canonical signal candidates.",
            )

    @property
    def strategy_metadata(self) -> StrategyMetadata:
        return self._strategy_metadata

    @property
    def performance_stats(self) -> Mapping[str, Any]:
        return {
            **self._performance_stats,
            "candidates_generated": self._candidates_generated,
            "by_type": dict(self._candidate_types_count),
        }

    async def on_configure(self) -> None:
        if self._context is not None:
            self._configuration = dict(self._context.config)

    async def publish_candidate(
        self,
        *,
        symbol: str,
        timeframe: str,
        generated_at: datetime | None = None,
        confidence: float,
        reasoning: Sequence[str],
        supporting_evidence: Mapping[str, float | str | bool],
        reward_risk_estimate: float,
        candidate_type: str = "TradeSignalCandidate",
    ) -> None:
        candidate_time = generated_at or datetime.now(UTC)
        signal_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{self.metadata.plugin_id}:{symbol}:{timeframe}:{candidate_type}:{candidate_time.isoformat()}",
            )
        )
        candidate_cls = _CANDIDATE_CLASS_MAP[candidate_type]
        payload = candidate_cls(
            signal_id=signal_id,
            symbol=symbol,
            timeframe=timeframe,
            generated_at=candidate_time,
            confidence=confidence,
            reasoning=reasoning,
            supporting_evidence=supporting_evidence,
            reward_risk_estimate=reward_risk_estimate,
            source_event_type="TradeSignalGenerated",
        ).to_payload()

        await self.publish(TRADE_SIGNAL_GENERATED, payload=payload)
        self._candidates_generated += 1
        self._candidate_types_count[candidate_type] += 1

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()

        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if self._candidates_generated == 0:
            return self._create_health(
                HealthStatus.DEGRADED,
                f"Strategy {self.metadata.plugin_id} is running but has not generated candidates yet.",
            )
        return self._create_health(
            HealthStatus.HEALTHY,
            f"Strategy {self.metadata.plugin_id} is actively generating trading candidates.",
        )

    def _create_health(self, status: HealthStatus, message: str) -> PluginHealth:
        return PluginHealth(
            status=status,
            message=message,
            details={
                "strategy_id": self._strategy_metadata.strategy_id,
                "name": self._strategy_metadata.name,
                "version": self._strategy_metadata.version,
                "description": self._strategy_metadata.description,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.parameter_type,
                        "default": p.default_value,
                    }
                    for p in self._strategy_metadata.parameters
                ],
                **dict(self.performance_stats),
            },
        )
