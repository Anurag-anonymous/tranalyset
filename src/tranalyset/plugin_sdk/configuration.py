from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


class ConfigurationValidationError(ValueError):
    pass


_MISSING = object()


@dataclass(frozen=True, slots=True)
class ConfigurationField:
    name: str
    value_type: type[Any]
    required: bool = True
    default: Any = _MISSING

    def __post_init__(self) -> None:
        if not self.name:
            msg = "Configuration field name must not be empty."
            raise ValueError(msg)
        if not isinstance(self.value_type, type):
            msg = "Configuration field value_type must be a type."
            raise TypeError(msg)


@dataclass(frozen=True, slots=True)
class PluginConfiguration:
    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def get(self, key: str, expected_type: type[Any]) -> Any:
        value = self.values[key]
        if not isinstance(value, expected_type):
            msg = f"Configuration value {key!r} is not {expected_type.__name__}."
            raise ConfigurationValidationError(msg)
        return value


class PluginConfigurationSpec:
    def __init__(self, fields: tuple[ConfigurationField, ...] = ()) -> None:
        self._fields = fields

    @property
    def fields(self) -> tuple[ConfigurationField, ...]:
        return self._fields

    def validate(self, raw_config: Mapping[str, Any]) -> PluginConfiguration:
        values: dict[str, Any] = {}
        for field_definition in self._fields:
            if field_definition.name in raw_config:
                value = raw_config[field_definition.name]
            elif field_definition.default is not _MISSING:
                value = field_definition.default
            elif field_definition.required:
                msg = f"Missing required configuration value: {field_definition.name}"
                raise ConfigurationValidationError(msg)
            else:
                continue

            if not isinstance(value, field_definition.value_type):
                msg = (
                    f"Configuration value {field_definition.name!r} must be "
                    f"{field_definition.value_type.__name__}."
                )
                raise ConfigurationValidationError(msg)
            values[field_definition.name] = value

        unknown_keys = set(raw_config) - {field_definition.name for field_definition in self._fields}
        if unknown_keys:
            msg = f"Unknown configuration values: {sorted(unknown_keys)}"
            raise ConfigurationValidationError(msg)

        return PluginConfiguration(values)
