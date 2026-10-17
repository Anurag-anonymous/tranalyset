from __future__ import annotations

from collections import Counter

from tranalyset.contracts.events import EventEnvelope
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.learning.contracts import (
    LEARNING_INSIGHTS_GENERATED,
    LEARNING_UPDATED,
    PUBLISHED_EVENTS,
    STRATEGY_IMPROVED,
    STRATEGY_PERFORMANCE_UPDATED,
    LearningInsightsGeneratedPayload,
    LearningUpdatedPayload,
    StrategyImprovedPayload,
    StrategyPerformanceUpdatedPayload,
)
from tranalyset.plugins.trade_manager.contracts import TRADE_CLOSED, TRADE_MODIFIED, TRADE_OPENED


class LearningPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="learning",
                name="Learning",
                version="1.0.0",
            )
        )
        self._trade_opened_seen = 0
        self._trade_modified_seen = 0
        self._trade_closed_seen = 0
        self._closed_trades: list[dict[str, object]] = []

    async def on_install(self, context: PluginInstallContext) -> None:
        for event_type in PUBLISHED_EVENTS:
            context.register_event(
                event_type,
                description=f"Learning plugin publishes {event_type}.",
            )

    @subscribes_to(TRADE_OPENED)
    async def handle_trade_opened(self, event: EventEnvelope) -> None:
        self._trade_opened_seen += 1

    @subscribes_to(TRADE_MODIFIED)
    async def handle_trade_modified(self, event: EventEnvelope) -> None:
        self._trade_modified_seen += 1

    @subscribes_to(TRADE_CLOSED)
    async def handle_trade_closed(self, event: EventEnvelope) -> None:
        self._trade_closed_seen += 1
        self._closed_trades.append(_trade_summary(event.payload))
        await self._publish_insights_and_performance()

    async def publish_learning_updated(self, payload: LearningUpdatedPayload) -> None:
        await self.publish(LEARNING_UPDATED, payload=payload.to_payload())

    async def publish_strategy_improved(self, payload: StrategyImprovedPayload) -> None:
        await self.publish(STRATEGY_IMPROVED, payload=payload.to_payload())

    async def publish_learning_insights_generated(self, payload: LearningInsightsGeneratedPayload) -> None:
        await self.publish(LEARNING_INSIGHTS_GENERATED, payload=payload.to_payload())

    async def publish_strategy_performance_updated(self, payload: StrategyPerformanceUpdatedPayload) -> None:
        await self.publish(STRATEGY_PERFORMANCE_UPDATED, payload=payload.to_payload())

    async def _publish_insights_and_performance(self) -> None:
        if not self._closed_trades:
            return
        trade_summaries = list(self._closed_trades)
        best_setups = tuple(
            {
                "symbol": summary["symbol"],
                "market_regime": summary.get("market_regime"),
                "pattern": summary.get("pattern"),
                "indicator": summary.get("indicator"),
                "reward_risk": summary.get("reward_risk"),
            }
            for summary in sorted(
                trade_summaries,
                key=lambda item: float(item.get("reward_risk", 0.0)),
                reverse=True,
            )[:3]
        )
        worst_setups = tuple(
            {
                "symbol": summary["symbol"],
                "market_regime": summary.get("market_regime"),
                "pattern": summary.get("pattern"),
                "indicator": summary.get("indicator"),
                "reward_risk": summary.get("reward_risk"),
            }
            for summary in sorted(
                trade_summaries,
                key=lambda item: float(item.get("reward_risk", 0.0)),
            )[:3]
        )
        optimal_holding_time_hours = _average(trade_summaries, "holding_time_hours")
        optimal_rr = _average(trade_summaries, "reward_risk")
        common_mistakes = tuple(
            mistake
            for mistake, _count in Counter(
                _mistake_label(summary) for summary in trade_summaries if _mistake_label(summary)
            ).most_common(3)
        )
        learning_insights = (
            "Favor setups with strong reward-risk characteristics.",
            "Review holding time against regime and pattern context.",
        )
        await self.publish_learning_insights_generated(
            LearningInsightsGeneratedPayload(
                generated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                best_setups=best_setups,
                worst_setups=worst_setups,
                optimal_holding_time_hours=optimal_holding_time_hours,
                optimal_rr=optimal_rr,
                common_mistakes=common_mistakes or ("Missing stop loss discipline.",),
                learning_insights=learning_insights,
            )
        )
        await self.publish_strategy_performance_updated(
            StrategyPerformanceUpdatedPayload(
                updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                win_rate=_win_rate(trade_summaries),
                average_reward_risk=optimal_rr,
                average_holding_time_hours=optimal_holding_time_hours,
                best_regime=_most_common(trade_summaries, "market_regime"),
                best_pattern=_most_common(trade_summaries, "pattern"),
            )
        )

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if (
            self._trade_opened_seen == 0
            or self._trade_modified_seen == 0
            or self._trade_closed_seen == 0
        ):
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Learning plugin is running before complete trade lifecycle evidence.",
                details={
                    "trade_opened_seen": self._trade_opened_seen,
                    "trade_modified_seen": self._trade_modified_seen,
                    "trade_closed_seen": self._trade_closed_seen,
                },
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Learning plugin is receiving complete trade lifecycle evidence.",
            details={
                "trade_opened_seen": self._trade_opened_seen,
                "trade_modified_seen": self._trade_modified_seen,
                "trade_closed_seen": self._trade_closed_seen,
            },
        )


def _trade_summary(payload: dict[str, object]) -> dict[str, object]:
    return {
        "symbol": payload.get("symbol", "UNKNOWN"),
        "market_regime": payload.get("market_regime"),
        "pattern": payload.get("pattern"),
        "indicator": payload.get("indicator"),
        "holding_time_hours": float(payload.get("holding_time_hours", 0.0)),
        "reward_risk": float(payload.get("reward_risk", 0.0)),
        "stop_loss_price": payload.get("stop_loss_price"),
    }


def _average(trades: list[dict[str, object]], key: str) -> float:
    values = [float(trade[key]) for trade in trades if key in trade and trade[key] is not None]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _mistake_label(trade: dict[str, object]) -> str | None:
    if trade.get("stop_loss_price") is None:
        return "Missing stop loss discipline."
    return None


def _win_rate(trades: list[dict[str, object]]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for trade in trades if float(trade.get("reward_risk", 0.0)) >= 1.0)
    return round(wins / len(trades), 4)


def _most_common(trades: list[dict[str, object]], key: str) -> str | None:
    values = [str(trade[key]) for trade in trades if key in trade and trade[key] is not None]
    if not values:
        return None
    counts = Counter(values)
    return counts.most_common(1)[0][0]


def create_plugin() -> LearningPlugin:
    return LearningPlugin()
