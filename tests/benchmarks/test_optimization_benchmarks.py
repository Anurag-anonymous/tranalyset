"""Benchmark comparisons for Phase 1 optimizations.

Run with: python -m pytest tests/benchmarks/ -v --tb=short -s
"""

import math
import time

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import PluginMetadata
from tranalyset.kernel.event_bus import AsyncEventBus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugin_sdk import BasePlugin
from tranalyset.plugins.signal_engine import SignalEnginePlugin
from tranalyset.plugins.trade_health_engine import TRADE_HEALTH_UPDATED


class _BenchPlugin(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id=plugin_id,
                name=f"Bench {plugin_id}",
                version="1.0.0",
            )
        )


@pytest.fixture
def event_type() -> EventType:
    return EventType("test.event")


@pytest.mark.asyncio
async def test_benchmark_subscription_cache(event_type: EventType) -> None:
    """Benchmark: subscription dispatch with cache.

    Measures dispatch throughput for 10,000 events with 10 subscribers.
    """
    bus = AsyncEventBus(worker_count=4)
    received: list[EventEnvelope] = []

    # Register 10 unique subscribers with distinct handler objects
    for idx in range(10):
        async def make_handler(event: EventEnvelope, _idx: int = idx) -> None:
            received.append(event)
        await bus.subscribe(event_type, make_handler)

    await bus.start()

    # Warm up
    for _ in range(100):
        await bus.publish(EventEnvelope.create(source="test", event_type=event_type))
    await bus.wait_until_idle()
    received.clear()

    # Benchmark: publish 10,000 events
    count = 10_000
    start = time.monotonic()
    for _ in range(count):
        await bus.publish(EventEnvelope.create(source="test", event_type=event_type))
    await bus.wait_until_idle()
    elapsed = time.monotonic() - start

    await bus.stop()

    events_per_second = (count * 10) / elapsed  # 10 handlers per event
    print(f"\n  Subscription cache benchmark: {events_per_second:,.0f} handler invocations/sec")
    print(f"  Total time for {count} events x 10 handlers: {elapsed:.3f}s")

    # Verify delivery
    assert len(received) == count * 10


@pytest.mark.asyncio
async def test_benchmark_parallel_health_checks() -> None:
    """Benchmark: parallel health checks.

    Measures time to check health of 10 plugins.
    """
    plugins = [_BenchPlugin(f"bench_plugin_{i}") for i in range(10)]
    kernel = Microkernel(plugin_loader=StaticPluginLoader(plugins))
    await kernel.start()

    # Warm up
    await kernel.plugin_health_checks()

    # Benchmark parallel health checks
    count = 50_000
    start = time.perf_counter()
    for _ in range(count):
        await kernel.plugin_health_checks()
    elapsed = time.perf_counter() - start

    await kernel.stop()

    checks_per_second = (count * len(plugins)) / elapsed if elapsed > 0 else float("inf")
    print(f"\n  Parallel health checks: {checks_per_second:,.0f} checks/sec")
    print(f"  Total time for {count} rounds x {len(plugins)} plugins: {elapsed:.3f}s")


@pytest.mark.asyncio
async def test_benchmark_evidence_eviction() -> None:
    """Benchmark: evidence eviction prevents unbounded memory growth.

    Measures that evidence dict stays bounded under continuous event flow.
    """
    plugin = SignalEnginePlugin(evidence_ttl_seconds=0.01)
    published: list[EventEnvelope] = []

    async def mock_publish(event: EventEnvelope) -> None:
        published.append(event)

    context = type("Ctx", (), {"plugin_id": "signal_engine", "config": {}})()
    context.publish = mock_publish  # type: ignore[method-assign]
    context.resolve = lambda k: k  # type: ignore[method-assign]

    install_ctx = type("InstallCtx", (), {"plugin_id": "signal_engine"})()
    install_ctx.register_event = lambda *a, **kw: None  # type: ignore[method-assign]
    install_ctx.register_service = lambda *a, **kw: None  # type: ignore[method-assign]
    install_ctx.register_service_factory = lambda *a, **kw: None  # type: ignore[method-assign]

    await plugin.install(install_ctx)  # type: ignore[arg-type]
    await plugin.configure(context)  # type: ignore[arg-type]
    await plugin.start(context)  # type: ignore[arg-type]

    # Simulate high-frequency events for many different trade keys
    for i in range(200):
        await plugin.handle_trade_health_updated(
            EventEnvelope.create(
                source="trade_health_engine",
                event_type=TRADE_HEALTH_UPDATED,
                payload={
                    "trade_id": f"trade-{i % 10}",
                    "symbol": "BENCH",
                    "timeframe": "1m",
                    "health_score": 50.0 + (i % 50),
                    "confidence_lower": 40.0,
                    "confidence_upper": 60.0,
                    "factors": {"trend": 50.0},
                    "explanations": ("Benchmark.",),
                },
            )
        )

    # With TTL-based eviction, the dict should not grow unboundedly
    assert len(plugin._evidence_by_key) <= 15  # 10 unique keys + some margin
    print(f"\n  Evidence dict size after 200 events: {len(plugin._evidence_by_key)}")
    print(f"  Total evictions: {plugin._evictions}")


@pytest.mark.asyncio
async def test_benchmark_strategy_class_map() -> None:
    """Benchmark: cached class map.

    Measures the overhead of the class_map dict creation in publish_candidate.
    """
    from tranalyset.strategy_sdk import StrategyPlugin, StrategyMetadata

    metadata = StrategyMetadata(
        strategy_id="bench_strat",
        name="Benchmark Strategy",
        version="1.0.0",
        description="Benchmark strategy for class map test.",
        parameters=(),
    )
    plugin = StrategyPlugin(metadata=metadata)
    published: list[EventEnvelope] = []

    async def mock_publish(event: EventEnvelope) -> None:
        published.append(event)

    context = type("Ctx", (), {"plugin_id": "bench_strat", "config": {}})()
    context.publish = mock_publish  # type: ignore[method-assign]
    context.resolve = lambda k: k  # type: ignore[method-assign]

    install_ctx = type("InstallCtx", (), {"plugin_id": "bench_strat"})()
    install_ctx.is_event_registered = lambda event_type: False  # type: ignore[method-assign]
    install_ctx.register_event = lambda *a, **kw: None  # type: ignore[method-assign]
    install_ctx.register_service = lambda *a, **kw: None  # type: ignore[method-assign]
    install_ctx.register_service_factory = lambda *a, **kw: None  # type: ignore[method-assign]

    await plugin.install(install_ctx)  # type: ignore[arg-type]
    await plugin.configure(context)  # type: ignore[arg-type]
    await plugin.start(context)  # type: ignore[arg-type]

    # Benchmark publish_candidate calls
    count = 5_000
    start = time.perf_counter()
    for _ in range(count):
        await plugin.publish_candidate(
            symbol="AAPL",
            timeframe="1m",
            confidence=0.75,
            reasoning=("Test reasoning.",),
            supporting_evidence={"health_score": 75.0},
            reward_risk_estimate=2.0,
            candidate_type="TradeSignalCandidate",
        )
    elapsed = time.perf_counter() - start

    calls_per_second = count / elapsed if elapsed > 0 else float("inf")
    print(f"\n  Cached class map: {calls_per_second:,.0f} calls/sec")
    print(f"  Total time for {count} calls: {elapsed:.3f}s")
    assert len(published) == count