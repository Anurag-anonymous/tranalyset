"""Unit tests for PluginLoader implementations."""
import pytest

from tranalyset.contracts.plugins import Plugin, PluginMetadata
from tranalyset.kernel.loader import ImportPathPluginLoader, PluginLoadError, StaticPluginLoader


class _MinimalPlugin:
    def __init__(self) -> None:
        self._metadata = PluginMetadata(
            plugin_id="minimal",
            name="Minimal Plugin",
            version="1.0.0",
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    def subscriptions(self):
        return {}

    async def install(self, context):
        return None

    async def configure(self, context):
        return None

    async def start(self, context):
        return None

    async def stop(self, context):
        return None

    async def health_check(self):
        from tranalyset.contracts.plugins import HealthStatus, PluginHealth
        return PluginHealth(status=HealthStatus.HEALTHY, message="OK.")


def test_static_plugin_loader_yields_given_plugins() -> None:
    plugin = _MinimalPlugin()
    loader = StaticPluginLoader([plugin])
    loaded = list(loader.load())

    assert len(loaded) == 1
    assert loaded[0] is plugin


def test_static_plugin_loader_yields_multiple() -> None:
    a = _MinimalPlugin()
    a._metadata = PluginMetadata(plugin_id="a", name="A", version="1.0.0")
    b = _MinimalPlugin()
    b._metadata = PluginMetadata(plugin_id="b", name="B", version="1.0.0")
    loader = StaticPluginLoader([a, b])

    loaded = list(loader.load())
    assert len(loaded) == 2


def test_import_path_loader_requires_valid_format() -> None:
    loader = ImportPathPluginLoader(["invalid_path"])
    with pytest.raises(PluginLoadError, match="module:attribute"):
        list(loader.load())


def test_import_path_loader_rejects_missing_module() -> None:
    loader = ImportPathPluginLoader(["nonexistent.module:plugin"])
    with pytest.raises((PluginLoadError, ModuleNotFoundError)):
        list(loader.load())


def test_import_path_loader_rejects_non_plugin() -> None:
    loader = ImportPathPluginLoader(["tranalyset.contracts.events:EventType"])
    with pytest.raises(PluginLoadError, match="does not implement Plugin"):
        list(loader.load())