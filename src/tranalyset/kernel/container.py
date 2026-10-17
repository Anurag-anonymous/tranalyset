from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ServiceNotFoundError(LookupError):
    pass


class ServiceAlreadyRegisteredError(ValueError):
    pass


@dataclass(slots=True)
class _FactoryBinding:
    factory: Callable[["ServiceContainer"], object]
    singleton: bool
    instance: object | None = None


class ServiceContainer:
    def __init__(self) -> None:
        self._instances: dict[object, object] = {}
        self._factories: dict[object, _FactoryBinding] = {}

    def bind_instance(self, key: object, instance: object, *, replace: bool = False) -> None:
        self._ensure_available(key, replace=replace)
        self._instances[key] = instance
        self._factories.pop(key, None)

    def bind_factory(
        self,
        key: object,
        factory: Callable[["ServiceContainer"], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        self._ensure_available(key, replace=replace)
        self._factories[key] = _FactoryBinding(factory=factory, singleton=singleton)
        self._instances.pop(key, None)

    def resolve(self, key: object) -> object:
        if key in self._instances:
            return self._instances[key]

        binding = self._factories.get(key)
        if binding is None:
            msg = f"Service is not registered: {key!r}"
            raise ServiceNotFoundError(msg)

        if binding.singleton and binding.instance is not None:
            return binding.instance

        instance = binding.factory(self)
        if binding.singleton:
            binding.instance = instance
        return instance

    def resolve_typed(self, key: type[Any]) -> Any:
        return self.resolve(key)

    def _ensure_available(self, key: object, *, replace: bool) -> None:
        if replace:
            return
        if key in self._instances or key in self._factories:
            msg = f"Service is already registered: {key!r}"
            raise ServiceAlreadyRegisteredError(msg)
