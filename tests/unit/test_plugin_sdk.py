from collections.abc import Awaitable, Callable, Mapping

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginInstallContext, PluginMetadata
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugin_sdk import (
    BasePlugin,
    ConfigurationField,
    ConfigurationValidationError,
    PluginConfigurationSpec,
    PluginRuntimeError,
    PluginVersion,
    subscribes_to,
)


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "sdk.test"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "sdk.test"
        self.events: list[EventType] = []
        self.services: dict[object, object] = {}

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        self.services[key] = instance

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[object], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        self.services[key] = factory


class SdkPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="sdk.test",
                name="SDK Test",
                version="1.2.3",
            ),
            configuration_spec=PluginConfigurationSpec(
                (
                    ConfigurationField("enabled", bool),
                    ConfigurationField("batch_size", int, default=10),
                )
            ),
        )
        self.received: list[EventEnvelope] = []
        self.lifecycle: list[str] = []

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_event(
            EventType("sdk.output"),
            description="SDK test plugin output event.",
        )
        self.lifecycle.append("install")

    async def on_configure(self) -> None:
        self.lifecycle.append("configure")

    async def on_start(self) -> None:
        self.lifecycle.append("start")

    async def on_shutdown(self) -> None:
        self.lifecycle.append("shutdown")

    @subscribes_to(EventType("sdk.input"))
    async def handle_input(self, event: EventEnvelope) -> None:
        self.received.append(event)


def test_plugin_version_parses_semantic_versions() -> None:
    assert str(PluginVersion.parse("1.2.3-alpha.1")) == "1.2.3-alpha.1"


def test_base_plugin_rejects_invalid_semantic_version() -> None:
    with pytest.raises(ValueError, match="semantic versioning"):
        BasePlugin(
            metadata=PluginMetadata(
                plugin_id="invalid",
                name="Invalid",
                version="1",
            )
        )


async def test_base_plugin_validates_configuration_and_health() -> None:
    plugin = SdkPlugin()
    install_context = InstallContext()
    runtime_context = CapturingContext(config={"enabled": True})

    await plugin.install(install_context)
    await plugin.configure(runtime_context)
    configured_health = await plugin.health_check()
    await plugin.start(runtime_context)
    running_health = await plugin.health_check()
    await plugin.stop(runtime_context)

    assert install_context.events == [EventType("sdk.output")]
    assert plugin.configuration.values == {"enabled": True, "batch_size": 10}
    assert configured_health.status is HealthStatus.DEGRADED
    assert running_health.status is HealthStatus.HEALTHY
    assert plugin.lifecycle == ["install", "configure", "start", "shutdown"]


async def test_base_plugin_rejects_invalid_configuration() -> None:
    plugin = SdkPlugin()

    await plugin.install(InstallContext())

    with pytest.raises(ConfigurationValidationError, match="enabled"):
        await plugin.configure(CapturingContext(config={"enabled": "yes"}))


async def test_base_plugin_declares_decorated_subscriptions() -> None:
    plugin = SdkPlugin()

    subscriptions = plugin.subscriptions()

    assert tuple(subscriptions) == (EventType("sdk.input"),)
    assert len(subscriptions[EventType("sdk.input")]) == 1


async def test_base_plugin_publishes_through_runtime_context() -> None:
    plugin = SdkPlugin()
    context = CapturingContext(config={"enabled": True})

    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    await plugin.publish(EventType("sdk.output"), payload={"ok": True})

    assert context.published[0].source == "sdk.test"
    assert context.published[0].event_type == EventType("sdk.output")
    assert context.published[0].payload["ok"] is True


async def test_base_plugin_requires_installation_before_startup() -> None:
    plugin = SdkPlugin()
    context = CapturingContext(config={"enabled": True})

    await plugin.configure(context)

    with pytest.raises(PluginRuntimeError, match="installed"):
        await plugin.start(context)


async def test_sdk_plugin_runs_inside_microkernel() -> None:
    plugin = SdkPlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader([plugin]),
        plugin_config={"sdk.test": {"enabled": True, "batch_size": 3}},
    )

    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(source="test", event_type=EventType("sdk.input"))
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("sdk.test")
    await kernel.stop()

    assert plugin.received[0].event_type == EventType("sdk.input")
    assert health.status is HealthStatus.HEALTHY
