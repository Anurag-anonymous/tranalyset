from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import (
    EventHandler,
    HealthStatus,
    PluginContext,
    PluginHealth,
    PluginInstallContext,
    PluginMetadata,
)
from tranalyset.plugin_sdk.configuration import PluginConfiguration, PluginConfigurationSpec
from tranalyset.plugin_sdk.versioning import PluginVersion


class PluginRuntimeError(RuntimeError):
    pass


def subscribes_to(event_type: EventType) -> Any:
    if not str(event_type):
        msg = "Subscription event type must not be empty."
        raise ValueError(msg)

    def decorator(handler: EventHandler) -> EventHandler:
        setattr(handler, "__tranalyset_event_type__", event_type)
        return handler

    return decorator


class BasePlugin:
    def __init__(
        self,
        *,
        metadata: PluginMetadata,
        configuration_spec: PluginConfigurationSpec | None = None,
    ) -> None:
        PluginVersion.parse(metadata.version)
        self._metadata = metadata
        self._configuration_spec = configuration_spec or PluginConfigurationSpec()
        self._configuration = PluginConfiguration()
        self._context: PluginContext | None = None
        self._installed = False
        self._configured = False
        self._running = False

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def configuration(self) -> PluginConfiguration:
        return self._configuration

    def subscriptions(self) -> Mapping[EventType, Sequence[EventHandler]]:
        subscriptions: dict[EventType, list[EventHandler]] = defaultdict(list)
        for member in self._iter_subscription_members():
            source = getattr(member, "__func__", member)
            event_type = getattr(source, "__tranalyset_event_type__")
            subscriptions[event_type].append(member)
        return {event_type: tuple(handlers) for event_type, handlers in subscriptions.items()}

    async def install(self, context: PluginInstallContext) -> None:
        await self.on_install(context)
        self._installed = True

    async def configure(self, context: PluginContext) -> None:
        self._configuration = self._configuration_spec.validate(context.config)
        self._context = context
        await self.on_configure()
        self._configured = True

    async def start(self, context: PluginContext) -> None:
        if not self._installed:
            msg = f"Plugin {self.metadata.plugin_id!r} must be installed before startup."
            raise PluginRuntimeError(msg)
        if not self._configured:
            msg = f"Plugin {self.metadata.plugin_id!r} must be configured before startup."
            raise PluginRuntimeError(msg)
        self._context = context
        await self.on_start()
        self._running = True

    async def stop(self, context: PluginContext) -> None:
        self._context = context
        await self.on_shutdown()
        self._running = False

    async def health_check(self) -> PluginHealth:
        if self._running:
            return PluginHealth(
                status=HealthStatus.HEALTHY,
                message=f"Plugin {self.metadata.plugin_id} is running.",
            )
        if self._configured:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message=f"Plugin {self.metadata.plugin_id} is configured but not running.",
            )
        return PluginHealth(
            status=HealthStatus.UNHEALTHY,
            message=f"Plugin {self.metadata.plugin_id} is not configured.",
        )

    async def publish(
        self,
        event_type: EventType,
        *,
        payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if self._context is None:
            msg = f"Plugin {self.metadata.plugin_id!r} has no runtime context."
            raise PluginRuntimeError(msg)
        await self._context.publish(
            EventEnvelope.create(
                source=self.metadata.plugin_id,
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                timestamp=_payload_timestamp(payload),
            )
        )

    async def on_install(self, context: PluginInstallContext) -> None:
        return None

    async def on_configure(self) -> None:
        return None

    async def on_start(self) -> None:
        return None

    async def on_shutdown(self) -> None:
        return None

    def _iter_subscription_members(self) -> tuple[EventHandler, ...]:
        members: list[EventHandler] = []
        for _, member in inspect.getmembers(self, predicate=callable):
            source = getattr(member, "__func__", member)
            if hasattr(source, "__tranalyset_event_type__"):
                if not inspect.iscoroutinefunction(member):
                    msg = "Event subscription handlers must be async methods."
                    raise TypeError(msg)
                members.append(member)
        return tuple(members)


def _payload_timestamp(payload: Mapping[str, Any] | None) -> datetime | None:
    if payload is None:
        return None
    for key in (
        "occurred_at",
        "observed_at",
        "decision_at",
        "generated_at",
        "closed_at",
        "received_at",
        "timestamp",
        "approved_at",
        "rejected_at",
        "calculated_at",
        "updated_at",
        "started_at",
        "completed_at",
    ):
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    return None
