from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import (
    HealthStatus,
    Plugin,
    PluginContext,
    PluginHealth,
    PluginId,
    PluginInstallContext,
    PluginState,
)
from tranalyset.kernel.container import ServiceContainer
from tranalyset.kernel.event_catalog import EventCatalog, EventRegistration
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.kernel.event_bus import Subscription
from tranalyset.kernel.loader import PluginLoader
from tranalyset.kernel.plugin_registry import PluginRegistry


@dataclass(frozen=True, slots=True)
class PluginRuntime:
    plugin: Plugin
    state: PluginState


class KernelLifecycleError(RuntimeError):
    pass


class _KernelPluginContext:
    def __init__(
        self,
        *,
        plugin_id: PluginId,
        publish_event: Callable[[EventEnvelope], Awaitable[None]],
        container: ServiceContainer,
        config: Mapping[str, Any],
    ) -> None:
        self._plugin_id = plugin_id
        self._publish_event = publish_event
        self._container = container
        self._config = MappingProxyType(dict(config))

    @property
    def plugin_id(self) -> PluginId:
        return self._plugin_id

    @property
    def config(self) -> Mapping[str, Any]:
        return self._config

    async def publish(self, event: EventEnvelope) -> None:
        await self._publish_event(event)

    def resolve(self, key: object) -> object:
        return self._container.resolve(key)


class _KernelPluginInstallContext:
    def __init__(self, *, plugin_id: PluginId, kernel: Microkernel) -> None:
        self._plugin_id = plugin_id
        self._kernel = kernel

    @property
    def plugin_id(self) -> PluginId:
        return self._plugin_id

    def is_event_registered(self, event_type: EventType) -> bool:
        return self._kernel.event_catalog.is_registered(event_type)

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
        self._kernel.register_event(
            event_type,
            owner=self._plugin_id,
            description=description,
            replace=replace,
        )

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        self._kernel.register_service(key, instance, replace=replace)

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[ServiceContainer], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        self._kernel.register_service_factory(
            key,
            factory,
            singleton=singleton,
            replace=replace,
        )


class Microkernel:
    def __init__(
        self,
        *,
        event_bus: AsyncEventBus | None = None,
        container: ServiceContainer | None = None,
        plugin_loader: PluginLoader | None = None,
        plugin_config: Mapping[PluginId, Mapping[str, Any]] | None = None,
    ) -> None:
        self.event_bus = event_bus or AsyncEventBus()
        self.container = container or ServiceContainer()
        self.event_catalog = EventCatalog()
        self.registry = PluginRegistry()
        self._plugin_loader = plugin_loader
        self._plugin_config = MappingProxyType(
            {
                plugin_id: MappingProxyType(dict(config))
                for plugin_id, config in (plugin_config or {}).items()
            }
        )
        self._states: dict[PluginId, PluginState] = {}
        self._contexts: dict[PluginId, PluginContext] = {}
        self._install_contexts: dict[PluginId, PluginInstallContext] = {}
        self._subscriptions: dict[PluginId, list[Subscription]] = {}
        self._plugin_event_types: dict[PluginId, set[EventType]] = {}
        self._start_order: tuple[Plugin, ...] = ()
        self._running = False
        self._loaded_from_loader = False

        self.container.bind_instance(AsyncEventBus, self.event_bus)
        self.container.bind_instance(ServiceContainer, self.container)
        self.container.bind_instance(EventCatalog, self.event_catalog)

        # Market data repository - shared infrastructure, not a plugin
        from tranalyset.market_data.aggregator import TimeframeAggregationEngine
        from tranalyset.market_data.ingestion import MarketDataIngestionService
        from tranalyset.market_data.indicators import IndicatorCache
        from tranalyset.market_data.repository import InMemoryStorageBackend, MarketDataRepository
        self._market_data_backend = InMemoryStorageBackend()
        self._market_data_repository = MarketDataRepository(self._market_data_backend)
        self.container.bind_instance(MarketDataRepository, self._market_data_repository)
        self._market_data_ingestion = MarketDataIngestionService(self._market_data_repository)
        self.container.bind_instance(MarketDataIngestionService, self._market_data_ingestion)

        # Timeframe aggregation engine - shared infrastructure, not a plugin
        self._aggregation_engine = TimeframeAggregationEngine(
            repository=self._market_data_repository,
            event_bus=self.event_bus,
        )
        self.container.bind_instance(TimeframeAggregationEngine, self._aggregation_engine)

        # Indicator cache service - shared infrastructure, not a plugin
        self._indicator_cache = IndicatorCache(
            repository=self._market_data_repository,
            event_bus=self.event_bus,
        )
        self.container.bind_instance(IndicatorCache, self._indicator_cache)

        # Replay engine - shared infrastructure, not a plugin
        from tranalyset.market_data.replay import ReplayEngine
        self._replay_engine = ReplayEngine(
            repository=self._market_data_repository,
            event_bus=self.event_bus,
        )
        self.container.bind_instance(ReplayEngine, self._replay_engine)

        # Market calendar service - shared infrastructure, not a plugin
        from tranalyset.market_data.calendar import MarketCalendarService
        self._calendar_service = MarketCalendarService(
            repository=self._market_data_repository,
            event_bus=self.event_bus,
        )
        self.container.bind_instance(MarketCalendarService, self._calendar_service)

        # Corporate actions service - shared infrastructure, not a plugin
        from tranalyset.market_data.corporate_actions import CorporateActionsService
        self._corporate_actions_service = CorporateActionsService(
            repository=self._market_data_repository,
            event_bus=self.event_bus,
        )
        self.container.bind_instance(CorporateActionsService, self._corporate_actions_service)

        # Data quality engine - shared infrastructure, not a plugin
        from tranalyset.market_data.quality import DataQualityEngine
        self._data_quality_engine = DataQualityEngine(
            repository=self._market_data_repository,
            event_bus=self.event_bus,
        )
        self.container.bind_instance(DataQualityEngine, self._data_quality_engine)

    @property
    def is_running(self) -> bool:
        return self._running

    def register_plugin(self, plugin: Plugin) -> None:
        self._ensure_not_running("register plugins")
        self.registry.register(plugin)
        self._states[plugin.metadata.plugin_id] = PluginState.REGISTERED

    def load_plugins(self) -> None:
        self._ensure_not_running("load plugins")
        if self._plugin_loader is None or self._loaded_from_loader:
            return
        self.registry.register_many(self._plugin_loader.load())
        for plugin in self.registry.all():
            self._states.setdefault(plugin.metadata.plugin_id, PluginState.REGISTERED)
        self._loaded_from_loader = True

    async def unload_plugin(self, plugin_id: PluginId) -> None:
        dependents = self.registry.dependents_of(plugin_id)
        if dependents:
            msg = f"Cannot unload plugin {plugin_id!r}; registered dependents exist: {sorted(dependents)}"
            raise KernelLifecycleError(msg)

        plugin = self.registry.get(plugin_id)
        if self._states.get(plugin_id) is PluginState.RUNNING:
            await self._stop_plugin(plugin)

        await self._unsubscribe_plugin(plugin_id)
        self.registry.unregister(plugin_id)
        self._states.pop(plugin_id, None)
        self._contexts.pop(plugin_id, None)
        self._install_contexts.pop(plugin_id, None)
        self._subscriptions.pop(plugin_id, None)
        self._unregister_plugin_events(plugin_id)
        self._start_order = tuple(
            registered for registered in self._start_order if registered.metadata.plugin_id != plugin_id
        )

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        self._ensure_not_running("register services")
        self.container.bind_instance(key, instance, replace=replace)

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[ServiceContainer], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        self._ensure_not_running("register service factories")
        self.container.bind_factory(key, factory, singleton=singleton, replace=replace)

    def register_event(
        self,
        event_type: EventType,
        *,
        owner: str,
        description: str,
        replace: bool = False,
    ) -> None:
        self._ensure_not_running("register events")
        self.event_catalog.register(
            EventRegistration(event_type=event_type, owner=owner, description=description),
            replace=replace,
        )

    def registered_events(self) -> Mapping[EventType, EventRegistration]:
        return self.event_catalog.snapshot()

    def plugin_states(self) -> Mapping[PluginId, PluginState]:
        return MappingProxyType(dict(self._states))

    async def plugin_health(self, plugin_id: PluginId) -> PluginHealth:
        return await self.registry.get(plugin_id).health_check()

    async def plugin_health_checks(self) -> Mapping[PluginId, PluginHealth]:
        plugins = self.registry.all()
        results = await asyncio.gather(
            *(plugin.health_check() for plugin in plugins),
            return_exceptions=True,
        )
        checks: dict[PluginId, PluginHealth] = {}
        for i, plugin in enumerate(plugins):
            result = results[i]
            if isinstance(result, Exception):
                checks[plugin.metadata.plugin_id] = PluginHealth(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Health check failed: {result}",
                )
            elif isinstance(result, PluginHealth):
                checks[plugin.metadata.plugin_id] = result
        return MappingProxyType(checks)

    async def start(self) -> None:
        if self._running:
            return
        self.load_plugins()

        self._start_order = self.registry.resolve_start_order()

        try:
            await self._install_plugins()
            await self.event_bus.start()
            await self._configure_plugins()
            await self._subscribe_plugins()
            await self._aggregation_engine.start()
            await self._calendar_service.start()
            await self._start_plugins()
        except Exception:
            await self._mark_failed_and_stop()
            raise

        self._running = True

    async def stop(self) -> None:
        if not self.event_bus.is_running and not self._start_order:
            return

        for plugin in reversed(self._start_order):
            await self._stop_plugin(plugin)

        await self._aggregation_engine.stop()
        await self._calendar_service.stop()
        await self.event_bus.stop()
        self._running = False

    async def publish_event(self, event: EventEnvelope) -> None:
        if not self.event_catalog.is_registered(event.event_type):
            msg = f"Event is not registered: {event.event_type}"
            raise KernelLifecycleError(msg)
        await self.event_bus.publish(event)

    async def _install_plugins(self) -> None:
        for plugin in self._start_order:
            plugin_id = plugin.metadata.plugin_id
            context = _KernelPluginInstallContext(plugin_id=plugin_id, kernel=self)
            self._install_contexts[plugin_id] = context
            await plugin.install(context)
            self._states[plugin_id] = PluginState.INSTALLED

    async def _configure_plugins(self) -> None:
        for plugin in self._start_order:
            plugin_id = plugin.metadata.plugin_id
            context = self._context_for(plugin)
            self._contexts[plugin_id] = context
            await plugin.configure(context)
            self._states[plugin_id] = PluginState.CONFIGURED

    async def _subscribe_plugins(self) -> None:
        for plugin in self._start_order:
            plugin_id = plugin.metadata.plugin_id
            self._subscriptions.setdefault(plugin_id, [])
            self._plugin_event_types.setdefault(plugin_id, set())
            for event_type, handlers in plugin.subscriptions().items():
                self._register_plugin_event(plugin_id, event_type)
                for handler in handlers:
                    subscription = await self.event_bus.subscribe(event_type, handler)
                    self._subscriptions[plugin_id].append(subscription)

    async def _start_plugins(self) -> None:
        for plugin in self._start_order:
            plugin_id = plugin.metadata.plugin_id
            self._states[plugin_id] = PluginState.STARTING
            await plugin.start(self._contexts[plugin_id])
            self._states[plugin_id] = PluginState.RUNNING

    async def _stop_plugin(self, plugin: Plugin) -> None:
        plugin_id = plugin.metadata.plugin_id
        if self._states.get(plugin_id) is not PluginState.RUNNING:
            return
        context = self._contexts[plugin_id]
        self._states[plugin_id] = PluginState.STOPPING
        try:
            await plugin.stop(context)
        finally:
            self._states[plugin_id] = PluginState.STOPPED

    async def _mark_failed_and_stop(self) -> None:
        for plugin in self._start_order:
            plugin_id = plugin.metadata.plugin_id
            if self._states.get(plugin_id) in {
                PluginState.REGISTERED,
                PluginState.INSTALLED,
                PluginState.CONFIGURED,
                PluginState.STARTING,
                PluginState.RUNNING,
            }:
                self._states[plugin_id] = PluginState.FAILED
        await self.stop()

    def _context_for(self, plugin: Plugin) -> PluginContext:
        plugin_id = plugin.metadata.plugin_id
        return _KernelPluginContext(
            plugin_id=plugin_id,
            publish_event=self.publish_event,
            container=self.container,
            config=self._plugin_config.get(plugin_id, {}),
        )

    def _register_plugin_event(self, plugin_id: PluginId, event_type: EventType) -> None:
        if not self.event_catalog.is_registered(event_type):
            self.event_catalog.register(
                EventRegistration(
                    event_type=event_type,
                    owner=plugin_id,
                    description=f"Event consumed by plugin {plugin_id}.",
                )
            )
        self._plugin_event_types.setdefault(plugin_id, set()).add(event_type)

    def _unregister_plugin_events(self, plugin_id: PluginId) -> None:
        for event_type in self._plugin_event_types.pop(plugin_id, set()):
            remaining_owner = any(
                event_type in event_types
                for owner, event_types in self._plugin_event_types.items()
                if owner != plugin_id
            )
            if not remaining_owner:
                registration = self.event_catalog.all().get(event_type)
                if registration is not None and registration.owner == plugin_id:
                    self.event_catalog.unregister(event_type)

    async def _unsubscribe_plugin(self, plugin_id: PluginId) -> None:
        for subscription in tuple(self._subscriptions.get(plugin_id, ())):
            await self.event_bus.unsubscribe(subscription)
        self._subscriptions[plugin_id] = []

    def _ensure_not_running(self, action: str) -> None:
        if self._running or self.event_bus.is_running:
            msg = f"Cannot {action} while the kernel is running."
            raise KernelLifecycleError(msg)
