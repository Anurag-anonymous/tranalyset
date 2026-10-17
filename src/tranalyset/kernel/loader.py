from __future__ import annotations

from collections.abc import Iterable
from importlib import import_module
from typing import Protocol

from tranalyset.contracts.plugins import Plugin


class PluginLoadError(RuntimeError):
    pass


class PluginLoader(Protocol):
    def load(self) -> Iterable[Plugin]:
        raise NotImplementedError


class StaticPluginLoader:
    def __init__(self, plugins: Iterable[Plugin]) -> None:
        self._plugins = tuple(plugins)

    def load(self) -> Iterable[Plugin]:
        return self._plugins


class ImportPathPluginLoader:
    def __init__(self, import_paths: Iterable[str]) -> None:
        self._import_paths = tuple(import_paths)

    def load(self) -> Iterable[Plugin]:
        for import_path in self._import_paths:
            yield self._load_one(import_path)

    def _load_one(self, import_path: str) -> Plugin:
        module_path, separator, attribute_name = import_path.partition(":")
        if not separator or not module_path or not attribute_name:
            msg = f"Plugin import path must be in 'module:attribute' format: {import_path!r}"
            raise PluginLoadError(msg)

        module = import_module(module_path)
        attribute = getattr(module, attribute_name, None)
        if attribute is None:
            msg = f"Plugin attribute does not exist: {import_path!r}"
            raise PluginLoadError(msg)

        plugin = attribute() if isinstance(attribute, type) else attribute
        if not isinstance(plugin, Plugin):
            msg = f"Loaded object does not implement Plugin protocol: {import_path!r}"
            raise PluginLoadError(msg)
        return plugin
