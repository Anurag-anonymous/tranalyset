from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import (
    EventHandler,
    HealthStatus,
    PluginContext,
    PluginHealth,
    PluginInstallContext,
    PluginMetadata,
    PluginState,
)
from tranalyset.kernel.kernel import KernelLifecycleError, Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.kernel.plugin_registry import PluginDependencyError, PluginRegistry


class RecordingPlugin:
    def __init__(
        self,
        plugin_id: str,
        recorder: list[str],
        *,
        depends_on: frozenset[str] = frozenset(),
        event_type: EventType | None = None,
        service_key: object | None = None,
        publish_on_start: EventType | None = None,
    ) -> None:
        self._metadata = PluginMetadata(
            plugin_id=plugin_id,
            name=plugin_id,
            version="1.0.0",
            depends_on=depends_on,
        )
        self._recorder = recorder
        self._event_type = event_type
        self._service_key = service_key
        self._publish_on_start = publish_on_start
        self.received: list[EventEnvelope] = []
        self.resolved_service: object | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    def subscriptions(self) -> Mapping[EventType, Sequence[EventHandler]]:
        if self._event_type is None:
            return {}
        return {self._event_type: (self.handle,)}

    async def install(self, context: PluginInstallContext) -> None:
        self._recorder.append(f"install:{self.metadata.plugin_id}")

    async def configure(self, context: PluginContext) -> None:
        self._recorder.append(f"configure:{self.metadata.plugin_id}")

    async def start(self, context: PluginContext) -> None:
        if self._service_key is not None:
            self.resolved_service = context.resolve(self._service_key)
        if self._publish_on_start is not None:
            await context.publish(
                EventEnvelope.create(
                    source=self.metadata.plugin_id,
                    event_type=self._publish_on_start,
                )
            )
        self._recorder.append(f"start:{self.metadata.plugin_id}")

    async def stop(self, context: PluginContext) -> None:
        self._recorder.append(f"stop:{self.metadata.plugin_id}")

    async def handle(self, event: EventEnvelope) -> None:
        self.received.append(event)

    async def health_check(self) -> PluginHealth:
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message=f"{self.metadata.plugin_id} is healthy.",
        )


async def test_kernel_starts_dependencies_before_dependents_and_stops_in_reverse() -> None:
    recorder: list[str] = []
    foundation = RecordingPlugin("foundation", recorder)
    dependent = RecordingPlugin("dependent", recorder, depends_on=frozenset({"foundation"}))

    kernel = Microkernel(plugin_loader=StaticPluginLoader([dependent, foundation]))

    await kernel.start()
    await kernel.stop()

    assert recorder == [
        "install:foundation",
        "install:dependent",
        "configure:foundation",
        "configure:dependent",
        "start:foundation",
        "start:dependent",
        "stop:dependent",
        "stop:foundation",
    ]
    assert kernel.plugin_states() == {
        "dependent": PluginState.STOPPED,
        "foundation": PluginState.STOPPED,
    }


async def test_kernel_starts_and_stops_calendar_infrastructure() -> None:
    kernel = Microkernel()

    await kernel.start()
    assert kernel._calendar_service._running
    assert kernel._aggregation_engine._running

    await kernel.stop()

    assert not kernel._calendar_service._running
    assert not kernel._aggregation_engine._running
    assert not kernel.event_bus.is_running


async def test_kernel_cleans_up_calendar_after_plugin_start_failure() -> None:
    class FailingStartPlugin(RecordingPlugin):
        async def start(self, context: PluginContext) -> None:
            raise RuntimeError("plugin startup failed")

    kernel = Microkernel(plugin_loader=StaticPluginLoader([FailingStartPlugin("failure", [])]))

    with pytest.raises(RuntimeError, match="plugin startup failed"):
        await kernel.start()

    assert not kernel._calendar_service._running
    assert not kernel._aggregation_engine._running
    assert not kernel.event_bus.is_running


async def test_kernel_wires_plugin_subscriptions_through_event_bus() -> None:
    recorder: list[str] = []
    event_type = EventType("platform.test")
    subscriber = RecordingPlugin("subscriber", recorder, event_type=event_type)

    kernel = Microkernel(plugin_loader=StaticPluginLoader([subscriber]))
    await kernel.start()
    event = EventEnvelope.create(source="test", event_type=event_type)
    await kernel.event_bus.publish(event)
    await kernel.event_bus.wait_until_idle()
    await kernel.stop()

    assert subscriber.received == [event]


async def test_kernel_rejects_missing_plugin_dependencies() -> None:
    recorder: list[str] = []
    plugin = RecordingPlugin("dependent", recorder, depends_on=frozenset({"missing"}))
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    with pytest.raises(PluginDependencyError, match="missing"):
        await kernel.start()


def test_kernel_loads_plugins_from_loader_without_starting() -> None:
    recorder: list[str] = []
    plugin = RecordingPlugin("plugin", recorder)
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))

    kernel.load_plugins()

    assert kernel.plugin_states() == {"plugin": PluginState.REGISTERED}
    assert recorder == []


def test_all_production_plugin_dependencies_resolve() -> None:
    plugin_directory = Path(__file__).resolve().parents[2] / "src" / "tranalyset" / "plugins"
    plugins = []
    for directory in sorted(plugin_directory.iterdir()):
        if not directory.is_dir() or not (directory / "plugin.py").exists():
            continue
        module = import_module(f"tranalyset.plugins.{directory.name}.plugin")
        factory = getattr(module, "create_plugin", None)
        if callable(factory):
            plugins.append(factory())

    assert plugins
    registry = PluginRegistry()
    registry.register_many(plugins)

    assert len(registry.resolve_start_order()) == len(plugins)


async def test_kernel_unloads_registered_plugin() -> None:
    recorder: list[str] = []
    plugin = RecordingPlugin("plugin", recorder)
    kernel = Microkernel()
    kernel.register_plugin(plugin)

    await kernel.unload_plugin("plugin")

    assert kernel.plugin_states() == {}


async def test_kernel_unloads_running_leaf_plugin_and_unsubscribes_handlers() -> None:
    recorder: list[str] = []
    event_type = EventType("platform.test")
    subscriber = RecordingPlugin("subscriber", recorder, event_type=event_type)
    kernel = Microkernel(plugin_loader=StaticPluginLoader([subscriber]))

    await kernel.start()
    await kernel.unload_plugin("subscriber")
    await kernel.event_bus.publish(EventEnvelope.create(source="test", event_type=event_type))
    await kernel.event_bus.wait_until_idle()
    await kernel.stop()

    assert subscriber.received == []
    assert recorder == [
        "install:subscriber",
        "configure:subscriber",
        "start:subscriber",
        "stop:subscriber",
    ]


async def test_kernel_refuses_to_unload_plugin_with_registered_dependents() -> None:
    recorder: list[str] = []
    foundation = RecordingPlugin("foundation", recorder)
    dependent = RecordingPlugin("dependent", recorder, depends_on=frozenset({"foundation"}))
    kernel = Microkernel(plugin_loader=StaticPluginLoader([foundation, dependent]))

    kernel.load_plugins()

    with pytest.raises(KernelLifecycleError, match="dependents"):
        await kernel.unload_plugin("foundation")


async def test_kernel_registers_services_for_plugin_context_resolution() -> None:
    recorder: list[str] = []
    service = object()
    plugin = RecordingPlugin("plugin", recorder, service_key="service")
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin]))
    kernel.register_service("service", service)

    await kernel.start()
    await kernel.stop()

    assert plugin.resolved_service is service


async def test_kernel_registers_events_and_routes_context_publications() -> None:
    recorder: list[str] = []
    event_type = EventType("platform.started")
    publisher = RecordingPlugin("publisher", recorder, publish_on_start=event_type)
    subscriber = RecordingPlugin("subscriber", recorder, event_type=event_type)
    kernel = Microkernel(plugin_loader=StaticPluginLoader([subscriber, publisher]))
    kernel.register_event(event_type, owner="platform", description="Platform startup event.")

    await kernel.start()
    await kernel.event_bus.wait_until_idle()
    await kernel.stop()

    assert subscriber.received[0].event_type == event_type


async def test_kernel_rejects_unregistered_context_publications() -> None:
    recorder: list[str] = []
    event_type = EventType("platform.unknown")
    publisher = RecordingPlugin("publisher", recorder, publish_on_start=event_type)
    kernel = Microkernel(plugin_loader=StaticPluginLoader([publisher]))

    with pytest.raises(KernelLifecycleError, match="not registered"):
        await kernel.start()


async def test_kernel_rejects_registration_while_running() -> None:
    kernel = Microkernel()

    await kernel.start()
    with pytest.raises(KernelLifecycleError, match="running"):
        kernel.register_service("late", object())
    await kernel.stop()


async def test_plugin_registry_caches_start_order() -> None:
    """Verify PluginRegistry caches the resolved start order and invalidates on mutation."""
    plugin_a = RecordingPlugin("plugin-a", [], event_type=EventType("test.event"))
    plugin_b = RecordingPlugin("plugin-b", [], depends_on=frozenset({"plugin-a"}), event_type=EventType("test.event"))
    kernel = Microkernel(plugin_loader=StaticPluginLoader([plugin_a, plugin_b]))
    kernel.load_plugins()

    # First resolve populates cache
    order1 = kernel.registry.resolve_start_order()
    assert len(order1) == 2
    assert order1[0].metadata.plugin_id == "plugin-a"

    # Second resolve returns cached result (same tuple)
    order2 = kernel.registry.resolve_start_order()
    assert order2 is order1  # Object identity confirms caching

    # Registering a new plugin invalidates cache
    plugin_c = RecordingPlugin("plugin-c", [], depends_on=frozenset({"plugin-b"}), event_type=EventType("test.event"))
    kernel.registry.register(plugin_c)
    order3 = kernel.registry.resolve_start_order()
    assert len(order3) == 3
    assert order3 is not order1  # Cache was invalidated

    await kernel.start()
    await kernel.stop()


async def test_event_bus_adaptive_worker_count() -> None:
    """Verify AsyncEventBus uses a reasonable default worker count."""
    from tranalyset.kernel.event_bus import AsyncEventBus

    bus = AsyncEventBus()
    assert bus._worker_count >= 1
    assert bus._worker_count <= 4
