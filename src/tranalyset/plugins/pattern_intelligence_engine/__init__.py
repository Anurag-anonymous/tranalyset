"""Pattern intelligence plugin and event contracts."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tranalyset.plugins.pattern_intelligence_engine.contracts import (
        PATTERN_DETECTED,
        PATTERN_INVALIDATED,
        PATTERN_UPDATED,
        PUBLISHED_EVENTS,
        SUBSCRIBED_EVENTS,
        PatternDetectedPayload,
        PatternInvalidatedPayload,
        PatternUpdatedPayload,
    )
    from tranalyset.plugins.pattern_intelligence_engine.plugin import (
        PatternIntelligenceEnginePlugin,
        create_plugin,
    )
    from tranalyset.plugins.pattern_intelligence_engine.service import (
        PatternCandidate,
        PatternIntelligenceService,
        PatternIntelligenceSettings,
    )


def __getattr__(name: str) -> object:
    contract_names = {
        "PATTERN_DETECTED",
        "PATTERN_INVALIDATED",
        "PATTERN_UPDATED",
        "PUBLISHED_EVENTS",
        "SUBSCRIBED_EVENTS",
        "PatternDetectedPayload",
        "PatternInvalidatedPayload",
        "PatternUpdatedPayload",
    }
    if name in contract_names:
        from tranalyset.plugins.pattern_intelligence_engine import contracts

        return getattr(contracts, name)
    if name in {"PatternIntelligenceEnginePlugin", "create_plugin"}:
        from tranalyset.plugins.pattern_intelligence_engine import plugin

        return getattr(plugin, name)
    if name in {"PatternCandidate", "PatternIntelligenceService", "PatternIntelligenceSettings"}:
        from tranalyset.plugins.pattern_intelligence_engine import service

        return getattr(service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PATTERN_DETECTED",
    "PATTERN_INVALIDATED",
    "PATTERN_UPDATED",
    "PUBLISHED_EVENTS",
    "SUBSCRIBED_EVENTS",
    "PatternCandidate",
    "PatternDetectedPayload",
    "PatternIntelligenceEnginePlugin",
    "PatternIntelligenceService",
    "PatternIntelligenceSettings",
    "PatternInvalidatedPayload",
    "PatternUpdatedPayload",
    "create_plugin",
]
