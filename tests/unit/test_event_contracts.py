from datetime import UTC, datetime

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType


def test_event_envelope_freezes_payload_and_metadata() -> None:
    event = EventEnvelope(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        source="test",
        event_type=EventType("platform.test"),
        payload={"levels": [1, 2], "nested": {"flag": True}},
        metadata={"trace_id": "abc"},
    )

    assert event.payload["levels"] == (1, 2)

    with pytest.raises(TypeError):
        event.payload["extra"] = "blocked"  # type: ignore[index]

    nested = event.payload["nested"]
    with pytest.raises(TypeError):
        nested["flag"] = False  # type: ignore[index]


def test_event_envelope_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        EventEnvelope(
            timestamp=datetime(2026, 1, 1),
            source="test",
            event_type=EventType("platform.test"),
        )
