"""Learning plugin."""

from tranalyset.plugins.learning.contracts import (
    LEARNING_INSIGHTS_GENERATED,
    LEARNING_UPDATED,
    STRATEGY_IMPROVED,
    STRATEGY_PERFORMANCE_UPDATED,
    LearningInsightsGeneratedPayload,
    LearningUpdatedPayload,
    StrategyImprovedPayload,
    StrategyPerformanceUpdatedPayload,
)
from tranalyset.plugins.learning.plugin import LearningPlugin, create_plugin

__all__ = [
    "LEARNING_INSIGHTS_GENERATED",
    "LEARNING_UPDATED",
    "STRATEGY_IMPROVED",
    "STRATEGY_PERFORMANCE_UPDATED",
    "LearningInsightsGeneratedPayload",
    "LearningPlugin",
    "LearningUpdatedPayload",
    "StrategyImprovedPayload",
    "StrategyPerformanceUpdatedPayload",
    "create_plugin",
]
