from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from tranalyset.contracts.plugins import Plugin, PluginId


class PluginRegistrationError(ValueError):
    pass


class PluginDependencyError(ValueError):
    pass


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[PluginId, Plugin] = {}
        self._cached_order: tuple[Plugin, ...] | None = None

    def register(self, plugin: Plugin) -> None:
        plugin_id = plugin.metadata.plugin_id
        if plugin_id in self._plugins:
            msg = f"Plugin is already registered: {plugin_id}"
            raise PluginRegistrationError(msg)
        self._plugins[plugin_id] = plugin
        self._cached_order = None

    def get(self, plugin_id: PluginId) -> Plugin:
        try:
            return self._plugins[plugin_id]
        except KeyError as error:
            msg = f"Plugin is not registered: {plugin_id}"
            raise PluginRegistrationError(msg) from error

    def unregister(self, plugin_id: PluginId) -> Plugin:
        if plugin_id not in self._plugins:
            msg = f"Plugin is not registered: {plugin_id}"
            raise PluginRegistrationError(msg)
        self._cached_order = None
        return self._plugins.pop(plugin_id)

    def all(self) -> tuple[Plugin, ...]:
        return tuple(self._plugins.values())

    def dependents_of(self, plugin_id: PluginId) -> tuple[PluginId, ...]:
        return tuple(
            plugin.metadata.plugin_id
            for plugin in self._plugins.values()
            if plugin_id in plugin.metadata.depends_on
        )

    def resolve_start_order(self) -> tuple[Plugin, ...]:
        if self._cached_order is not None:
            return self._cached_order
        self._validate_dependencies()

        incoming_counts = {plugin_id: 0 for plugin_id in self._plugins}
        dependents: dict[PluginId, list[PluginId]] = defaultdict(list)

        for plugin in self._plugins.values():
            for dependency in plugin.metadata.depends_on:
                incoming_counts[plugin.metadata.plugin_id] += 1
                dependents[dependency].append(plugin.metadata.plugin_id)

        ready = deque(
            plugin_id for plugin_id, incoming_count in incoming_counts.items() if incoming_count == 0
        )
        ordered: list[Plugin] = []

        while ready:
            plugin_id = ready.popleft()
            ordered.append(self._plugins[plugin_id])
            for dependent_id in dependents[plugin_id]:
                incoming_counts[dependent_id] -= 1
                if incoming_counts[dependent_id] == 0:
                    ready.append(dependent_id)

        if len(ordered) != len(self._plugins):
            msg = "Plugin dependency graph contains a cycle."
            raise PluginDependencyError(msg)
        self._cached_order = tuple(ordered)
        return self._cached_order

    def _validate_dependencies(self) -> None:
        registered = set(self._plugins)
        missing: dict[PluginId, set[PluginId]] = {}
        for plugin in self._plugins.values():
            unknown = set(plugin.metadata.depends_on) - registered
            if unknown:
                missing[plugin.metadata.plugin_id] = unknown

        if missing:
            details = ", ".join(
                f"{plugin_id} -> {sorted(dependencies)}"
                for plugin_id, dependencies in sorted(missing.items())
            )
            msg = f"Plugin dependencies are not registered: {details}"
            raise PluginDependencyError(msg)

    def register_many(self, plugins: Iterable[Plugin]) -> None:
        for plugin in plugins:
            self.register(plugin)
