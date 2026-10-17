from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def require_text(value: str, field_name: str) -> None:
    if not value:
        msg = f"{field_name} must not be empty."
        raise ValueError(msg)


def to_utc_iso(timestamp: datetime, *, field_name: str = "timestamp") -> str:
    if timestamp.tzinfo is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)
    return timestamp.astimezone(UTC).isoformat()


def optional_text_payload(payload: dict[str, Any], key: str, value: str | None) -> None:
    if value is not None:
        require_text(value, key)
        payload[key] = value
