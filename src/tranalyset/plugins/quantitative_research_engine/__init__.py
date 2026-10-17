"""Quantitative research engine plugin."""

from tranalyset.plugins.quantitative_research_engine.contracts import (
    OPTIMIZATION_COMPLETED,
    PUBLISHED_EVENTS,
    RESEARCH_COMPLETED,
    STRATEGY_EVALUATED,
)
from tranalyset.plugins.quantitative_research_engine.plugin import (
    QuantitativeResearchEnginePlugin,
    create_plugin,
)
from tranalyset.plugins.quantitative_research_engine.service import (
    QuantitativeResearchService,
    QuantitativeResearchSettings,
    ResearchReport,
    StrategyEvaluationResult,
)
from tranalyset.plugins.quantitative_research_engine.laboratory import (
    LaboratoryEvaluation,
    ResearchPeriod,
    ResearchSplit,
    StrategyEvaluationLaboratory,
    StrategyLaboratoryReport,
    StrategyResearchInput,
)

__all__ = [
    "OPTIMIZATION_COMPLETED",
    "PUBLISHED_EVENTS",
    "RESEARCH_COMPLETED",
    "STRATEGY_EVALUATED",
    "QuantitativeResearchEnginePlugin",
    "QuantitativeResearchService",
    "QuantitativeResearchSettings",
    "ResearchReport",
    "StrategyEvaluationResult",
    "LaboratoryEvaluation",
    "ResearchPeriod",
    "ResearchSplit",
    "StrategyEvaluationLaboratory",
    "StrategyLaboratoryReport",
    "StrategyResearchInput",
    "create_plugin",
]
