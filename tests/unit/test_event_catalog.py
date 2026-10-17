"""Unit tests for EventCatalog."""
import pytest

from tranalyset.contracts.events import EventType
from tranalyset.kernel.event_catalog import EventCatalog, EventRegistration, EventRegistrationError


def test_event_catalog_register_and_is_registered() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    registration = EventRegistration(
        event_type=event_type,
        owner="test_owner",
        description="A test event.",
    )

    catalog.register(registration)

    assert catalog.is_registered(event_type) is True


def test_event_catalog_rejects_duplicate() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    registration = EventRegistration(
        event_type=event_type,
        owner="test_owner",
        description="A test event.",
    )

    catalog.register(registration)
    with pytest.raises(EventRegistrationError, match="already registered"):
        catalog.register(registration)


def test_event_catalog_replaces_duplicate() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    reg1 = EventRegistration(event_type=event_type, owner="owner1", description="First.")
    reg2 = EventRegistration(event_type=event_type, owner="owner2", description="Second.")

    catalog.register(reg1)
    catalog.register(reg2, replace=True)

    assert catalog.is_registered(event_type) is True
    assert catalog.all()[event_type].owner == "owner2"


def test_event_catalog_unregister() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    catalog.register(EventRegistration(event_type=event_type, owner="test", description="Test."))

    catalog.unregister(event_type)

    assert catalog.is_registered(event_type) is False


def test_event_catalog_unregister_missing() -> None:
    catalog = EventCatalog()
    # Should not raise
    catalog.unregister(EventType("missing.event"))


def test_event_catalog_snapshot_cached() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    catalog.register(EventRegistration(event_type=event_type, owner="test", description="Test."))

    snap1 = catalog.snapshot()
    snap2 = catalog.snapshot()

    # Snapshot should be cached (same object)
    assert snap1 is snap2


def test_event_catalog_snapshot_invalidated_on_register() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    catalog.register(EventRegistration(event_type=event_type, owner="test", description="Test."))

    snap1 = catalog.snapshot()
    event_type2 = EventType("test.event2")
    catalog.register(EventRegistration(event_type=event_type2, owner="test2", description="Test2."))
    snap2 = catalog.snapshot()

    assert snap1 is not snap2
    assert len(snap2) == 2


def test_event_catalog_snapshot_invalidated_on_unregister() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    catalog.register(EventRegistration(event_type=event_type, owner="test", description="Test."))

    snap1 = catalog.snapshot()
    catalog.unregister(event_type)
    snap2 = catalog.snapshot()

    assert snap1 is not snap2
    assert len(snap2) == 0


def test_event_catalog_all_returns_copy() -> None:
    catalog = EventCatalog()
    event_type = EventType("test.event")
    catalog.register(EventRegistration(event_type=event_type, owner="test", description="Test."))

    all_events = catalog.all()
    assert len(all_events) == 1

    # Mutating the returned dict should not affect the catalog
    all_events.clear()
    assert catalog.is_registered(event_type) is True


def test_event_registration_validates_required_fields() -> None:
    with pytest.raises(ValueError, match="empty"):
        EventRegistration(event_type=EventType(""), owner="test", description="Test.")
    with pytest.raises(ValueError, match="empty"):
        EventRegistration(event_type=EventType("test.event"), owner="", description="Test.")
    with pytest.raises(ValueError, match="empty"):
        EventRegistration(event_type=EventType("test.event"), owner="test", description="")