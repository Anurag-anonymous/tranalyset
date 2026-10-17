"""Technical analysis plugin."""

from tranalyset.plugins.technical_analysis.contracts import (
    INDICATORS_UPDATED,
    MOMENTUM_CHANGED,
    STRUCTURE_CHANGED,
    TREND_CHANGED,
    IndicatorsUpdatedPayload,
    MomentumChangedPayload,
    StructureChangedPayload,
    TrendChangedPayload,
)
from tranalyset.plugins.technical_analysis.plugin import TechnicalAnalysisPlugin, create_plugin

__all__ = [
    "INDICATORS_UPDATED",
    "MOMENTUM_CHANGED",
    "STRUCTURE_CHANGED",
    "TREND_CHANGED",
    "IndicatorsUpdatedPayload",
    "MomentumChangedPayload",
    "StructureChangedPayload",
    "TechnicalAnalysisPlugin",
    "TrendChangedPayload",
    "create_plugin",
]
