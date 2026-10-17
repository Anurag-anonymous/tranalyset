from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, NewType

EventType = NewType("EventType", str)
ImmutableValue = str | int | float | bool | None | tuple["ImmutableValue", ...] | Mapping[str, "ImmutableValue"]


_MAX_FREEZE_DEPTH = 100


def _freeze_value(value: Any, *, _depth: int = 0) -> ImmutableValue:
    if _depth > _MAX_FREEZE_DEPTH:
        msg = "Event value nesting exceeds maximum depth."
        raise ValueError(msg)
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item, _depth=_depth + 1) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_freeze_value(item, _depth=_depth + 1) for item in value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(_freeze_value(item, _depth=_depth + 1) for item in value)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    msg = f"Unsupported event value type: {type(value).__name__}"
    raise TypeError(msg)


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, ImmutableValue]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    timestamp: datetime
    source: str
    event_type: EventType
    payload: Mapping[str, ImmutableValue] = field(default_factory=dict)
    metadata: Mapping[str, ImmutableValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            msg = "Event timestamp must be timezone-aware."
            raise ValueError(msg)
        if not self.source:
            msg = "Event source must not be empty."
            raise ValueError(msg)
        if not str(self.event_type):
            msg = "Event type must not be empty."
            raise ValueError(msg)

        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def frozen_payload(self) -> Mapping[str, ImmutableValue]:
        return self.payload

    @property
    def frozen_metadata(self) -> Mapping[str, ImmutableValue]:
        return self.metadata

    @classmethod
    def create(
        cls,
        *,
        source: str,
        event_type: EventType,
        payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> EventEnvelope:
        return cls(
            timestamp=timestamp or datetime.now(UTC),
            source=source,
            event_type=event_type,
            payload=payload or {},
            metadata=metadata or {},
        )
