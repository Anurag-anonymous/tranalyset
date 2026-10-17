from __future__ import annotations

import re
from dataclasses import dataclass


_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)


@dataclass(frozen=True, order=True, slots=True)
class PluginVersion:
    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    @classmethod
    def parse(cls, value: str) -> PluginVersion:
        match = _SEMVER_PATTERN.match(value)
        if match is None:
            msg = f"Plugin version must use semantic versioning: {value!r}"
            raise ValueError(msg)
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=match.group("prerelease"),
        )

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease is None:
            return base
        return f"{base}-{self.prerelease}"
