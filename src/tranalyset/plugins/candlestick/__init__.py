"""Candlestick pattern plugin."""

from tranalyset.plugins.candlestick.contracts import (
    PATTERN_DETECTED,
    PATTERN_INVALIDATED,
    PatternDetectedPayload,
    PatternInvalidatedPayload,
)
from tranalyset.plugins.candlestick.plugin import CandlestickPlugin, create_plugin

__all__ = [
    "PATTERN_DETECTED",
    "PATTERN_INVALIDATED",
    "CandlestickPlugin",
    "PatternDetectedPayload",
    "PatternInvalidatedPayload",
    "create_plugin",
]
