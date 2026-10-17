from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from tranalyset.contracts.plugins import PluginContext, PluginInstallContext


@dataclass(frozen=True, slots=True)
class StrategyParameter:
    name: str
    parameter_type: str
    default_value: Any
    min_value: Any | None = None
    max_value: Any | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            msg = "Parameter name must not be empty."
            raise ValueError(msg)
        if self.parameter_type not in {"float", "int", "bool", "str"}:
            msg = f"Unsupported parameter type: {self.parameter_type}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_id: str
    name: str
    version: str
    description: str
    parameters: tuple[StrategyParameter, ...] = ()

    def __post_init__(self) -> None:
        if not self.strategy_id:
            msg = "Strategy id must not be empty."
            raise ValueError(msg)
        if not self.name:
            msg = "Strategy name must not be empty."
            raise ValueError(msg)
        if not self.version:
            msg = "Strategy version must not be empty."
            raise ValueError(msg)
        if not self.description:
            msg = "Strategy description must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "parameters", tuple(self.parameters))


@runtime_checkable
class StrategyContext(Protocol):
    @property
    def strategy_id(self) -> str:
        raise NotImplementedError

    @property
    def config(self) -> dict[str, Any]:
        raise NotImplementedError

    async def publish_candidate(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def resolve(self, key: object) -> object:
        raise NotImplementedError


@runtime_checkable
class StrategyInstallContext(Protocol):
    @property
    def strategy_id(self) -> str:
        raise NotImplementedError

    def register_event(
        self,
        event_type: str,
        *,
        description: str,
    ) -> None:
        raise NotImplementedError
