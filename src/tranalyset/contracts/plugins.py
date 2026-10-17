from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from tranalyset.contracts.events import EventEnvelope, EventType

PluginId = str
EventHandler = Callable[[EventEnvelope], Awaitable[None]]


class PluginState(StrEnum):
    REGISTERED = "registered"
    INSTALLED = "installed"
    CONFIGURED = "configured"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    plugin_id: PluginId
    name: str
    version: str
    depends_on: frozenset[PluginId] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.plugin_id:
            msg = "Plugin id must not be empty."
            raise ValueError(msg)
        if not self.name:
            msg = "Plugin name must not be empty."
            raise ValueError(msg)
        if not self.version:
            msg = "Plugin version must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "depends_on", frozenset(self.depends_on))


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class PluginHealth:
    status: HealthStatus
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            msg = "Plugin health message must not be empty."
            raise ValueError(msg)


class PluginInstallContext(Protocol):
    @property
    def plugin_id(self) -> PluginId:
        raise NotImplementedError

    def is_event_registered(self, event_type: EventType) -> bool:
        raise NotImplementedError

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
        raise NotImplementedError

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        raise NotImplementedError

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[Any], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        raise NotImplementedError


class PluginContext(Protocol):
    @property
    def plugin_id(self) -> PluginId:
        raise NotImplementedError

    @property
    def config(self) -> Mapping[str, Any]:
        raise NotImplementedError

    async def publish(self, event: EventEnvelope) -> None:
        raise NotImplementedError

    def resolve(self, key: object) -> object:
        raise NotImplementedError


@runtime_checkable
class Plugin(Protocol):
    @property
    def metadata(self) -> PluginMetadata:
        raise NotImplementedError

    def subscriptions(self) -> Mapping[EventType, Sequence[EventHandler]]:
        raise NotImplementedError

    async def install(self, context: PluginInstallContext) -> None:
        raise NotImplementedError

    async def configure(self, context: PluginContext) -> None:
        raise NotImplementedError

    async def start(self, context: PluginContext) -> None:
        raise NotImplementedError

    async def stop(self, context: PluginContext) -> None:
        raise NotImplementedError

    async def health_check(self) -> PluginHealth:
        raise NotImplementedError
