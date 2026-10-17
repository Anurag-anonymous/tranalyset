# Plugin SDK

The Plugin SDK gives feature plugins a stable authoring surface while preserving the
microkernel boundary. Plugins depend on contracts and SDK primitives, not kernel internals.

## Responsibilities

A production plugin supports:

- installation,
- loading,
- configuration,
- startup,
- shutdown,
- event subscription,
- event publishing,
- health checks,
- metadata,
- semantic versioning.

## Base Plugin

Plugins should subclass `tranalyset.plugin_sdk.BasePlugin` and provide immutable
`PluginMetadata`.

```python
from tranalyset.contracts import EventType, PluginMetadata
from tranalyset.contracts.plugins import PluginInstallContext
from tranalyset.plugin_sdk import BasePlugin, ConfigurationField, PluginConfigurationSpec


class ExamplePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="example.plugin",
                name="Example Plugin",
                version="1.0.0",
            ),
            configuration_spec=PluginConfigurationSpec(
                (ConfigurationField("enabled", bool),)
            ),
        )

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_event(
            EventType("example.ready"),
            description="Emitted when the example plugin is ready.",
        )
```

`BasePlugin` implements the kernel-facing plugin protocol and exposes protected lifecycle hooks:

- `on_install`
- `on_configure`
- `on_start`
- `on_shutdown`

The kernel still owns lifecycle order and runtime state.

## Installation

Installation runs before the event bus starts. Use it to register event contracts and shared
contract-level services.

Plugins must not publish events during installation.

## Configuration

`PluginConfigurationSpec` validates raw plugin configuration before startup. Unknown fields,
missing required values, and wrong types fail configuration.

Validated configuration is available as `self.configuration`.

## Event Subscription

Use `@subscribes_to(EventType(...))` on async methods. Subscriptions are declarative and wired by
the kernel during startup.

```python
from tranalyset.contracts import EventEnvelope, EventType
from tranalyset.plugin_sdk import subscribes_to


@subscribes_to(EventType("market.evidence"))
async def handle_evidence(self, event: EventEnvelope) -> None:
    ...
```

Plugins never directly call other plugins.

## Event Publishing

After configuration has provided a runtime context, SDK plugins can publish registered events:

```python
await self.publish(EventType("example.ready"), payload={"ready": True})
```

The SDK automatically uses the plugin id as the event source.

## Health Checks

`BasePlugin.health_check()` reports:

- `healthy` when running,
- `degraded` when configured but stopped,
- `unhealthy` before configuration.

Plugins can override `health_check()` when they have real infrastructure to inspect.

## Versioning

SDK plugins must use semantic versions such as `1.0.0` or `1.0.0-alpha.1`. Invalid versions fail
at plugin construction.
