"""Signal orchestrator plugin for ranking and filtering trade candidates."""

from tranalyset.plugins.signal_orchestrator.contracts import (
    ACCEPTED_CANDIDATE,
    OrchestratedCandidatePayload,
    REJECTED_CANDIDATE,
    WATCH_CANDIDATE_GENERATED,
)
from tranalyset.plugins.signal_orchestrator.plugin import SignalOrchestratorPlugin, create_plugin

__all__ = [
    "ACCEPTED_CANDIDATE",
    "OrchestratedCandidatePayload",
    "REJECTED_CANDIDATE",
    "SignalOrchestratorPlugin",
    "WATCH_CANDIDATE_GENERATED",
    "create_plugin",
]