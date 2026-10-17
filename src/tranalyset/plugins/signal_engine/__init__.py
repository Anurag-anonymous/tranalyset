"""Signal engine plugin."""

from tranalyset.plugins.signal_engine.contracts import (
    CANDIDATE_TYPES,
    EntryCandidate,
    ExitCandidate,
    PartialExitCandidate,
    SignalCandidatePayload,
    TradeSignalCandidate,
    TrailCandidate,
    WatchCandidate,
)
from tranalyset.plugins.signal_engine.plugin import SignalEnginePlugin, create_plugin
from tranalyset.plugins.trade_manager.contracts import TRADE_SIGNAL_GENERATED

__all__ = [
    "CANDIDATE_TYPES",
    "TRADE_SIGNAL_GENERATED",
    "EntryCandidate",
    "ExitCandidate",
    "PartialExitCandidate",
    "SignalCandidatePayload",
    "SignalEnginePlugin",
    "TradeSignalCandidate",
    "TrailCandidate",
    "WatchCandidate",
    "create_plugin",
]
