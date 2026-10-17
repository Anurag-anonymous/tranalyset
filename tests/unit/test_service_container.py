import pytest

from tranalyset.kernel.container import (
    ServiceAlreadyRegisteredError,
    ServiceContainer,
    ServiceNotFoundError,
)


def test_container_resolves_bound_instances() -> None:
    container = ServiceContainer()
    service = object()

    container.bind_instance("service", service)

    assert container.resolve("service") is service


def test_container_caches_singleton_factory_results() -> None:
    container = ServiceContainer()
    container.bind_factory("service", lambda _: object())

    first = container.resolve("service")
    second = container.resolve("service")

    assert first is second


def test_container_rejects_duplicate_bindings() -> None:
    container = ServiceContainer()
    container.bind_instance("service", object())

    with pytest.raises(ServiceAlreadyRegisteredError):
        container.bind_instance("service", object())


def test_container_raises_for_missing_services() -> None:
    container = ServiceContainer()

    with pytest.raises(ServiceNotFoundError):
        container.resolve("missing")
