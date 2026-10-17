from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


def _require_aware(timestamp: datetime, field_name: str) -> None:
    if timestamp.tzinfo is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)


def _freeze_mapping(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: str
    source: str
    event_type: str
    occurred_at: datetime = field(default_factory=_now)
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            msg = "DomainEvent event_id must not be empty."
            raise ValueError(msg)
        if not self.source.strip():
            msg = "DomainEvent source must not be empty."
            raise ValueError(msg)
        if not self.event_type.strip():
            msg = "DomainEvent event_type must not be empty."
            raise ValueError(msg)
        _require_aware(self.occurred_at, "DomainEvent occurred_at")
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class TradeCreated(DomainEvent):
    event_type: str = "TradeCreated"


@dataclass(frozen=True, slots=True)
class TradeClosed(DomainEvent):
    event_type: str = "TradeClosed"


@dataclass(frozen=True, slots=True)
class PositionOpened(DomainEvent):
    event_type: str = "PositionOpened"


@dataclass(frozen=True, slots=True)
class PositionClosed(DomainEvent):
    event_type: str = "PositionClosed"


@dataclass(frozen=True, slots=True)
class MarketRegimeChanged(DomainEvent):
    event_type: str = "MarketRegimeChanged"


@dataclass(frozen=True, slots=True)
class TradeHealthChanged(DomainEvent):
    event_type: str = "TradeHealthChanged"


@dataclass(frozen=True, slots=True)
class RiskApproved(DomainEvent):
    event_type: str = "RiskApproved"


@dataclass(frozen=True, slots=True)
class PatternDetected(DomainEvent):
    event_type: str = "PatternDetected"


__all__ = [
    "DomainEvent",
    "TradeCreated",
    "TradeClosed",
    "PositionOpened",
    "PositionClosed",
    "MarketRegimeChanged",
    "TradeHealthChanged",
    "RiskApproved",
    "PatternDetected",
]
