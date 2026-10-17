from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from tranalyset.contracts.events import EventType


class EventRegistrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EventRegistration:
    event_type: EventType
    owner: str
    description: str

    def __post_init__(self) -> None:
        if not str(self.event_type):
            msg = "Event type must not be empty."
            raise ValueError(msg)
        if not self.owner:
            msg = "Event owner must not be empty."
            raise ValueError(msg)
        if not self.description:
            msg = "Event description must not be empty."
            raise ValueError(msg)


class EventCatalog:
    def __init__(self) -> None:
        self._events: dict[EventType, EventRegistration] = {}
        self._cached_snapshot: MappingProxyType[EventType, EventRegistration] | None = None

    def register(self, registration: EventRegistration, *, replace: bool = False) -> None:
        if not replace and registration.event_type in self._events:
            msg = f"Event is already registered: {registration.event_type}"
            raise EventRegistrationError(msg)
        self._events[registration.event_type] = registration
        self._cached_snapshot = None

    def unregister(self, event_type: EventType) -> None:
        self._events.pop(event_type, None)
        self._cached_snapshot = None

    def is_registered(self, event_type: EventType) -> bool:
        return event_type in self._events

    def all(self) -> dict[EventType, EventRegistration]:
        return dict(self._events)

    def snapshot(self) -> MappingProxyType[EventType, EventRegistration]:
        if self._cached_snapshot is None:
            self._cached_snapshot = MappingProxyType(dict(self._events))
        return self._cached_snapshot
