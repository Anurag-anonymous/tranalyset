from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.portfolio_engine import (
    CAPITAL_UPDATED,
    DRAWDOWN_CHANGED,
    EXPOSURE_CHANGED,
    PORTFOLIO_UPDATED,
    CapitalUpdatedPayload,
    DrawdownChangedPayload,
    ExposureChangedPayload,
    PortfolioEnginePlugin,
    PortfolioUpdatedPayload,
)
from tranalyset.plugins.portfolio_engine.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.portfolio_engine.state import PortfolioState
from tranalyset.plugins.risk import RISK_APPROVED
from tranalyset.plugins.trade_manager import TRADE_CLOSED, TRADE_MODIFIED, TRADE_OPENED
from tranalyset.plugins.paper_trading_engine import PAPER_PORTFOLIO_UPDATED, PAPER_TRADE_CLOSED


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "portfolio_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "portfolio_engine"
        self.events: list[EventType] = []

    def register_event(
        self,
        event_type: EventType,
        *,
        description: str,
        replace: bool = False,
    ) -> None:
        self.events.append(event_type)

    def register_service(self, key: object, instance: object, *, replace: bool = False) -> None:
        return None

    def register_service_factory(
        self,
        key: object,
        factory: Callable[[object], object],
        *,
        singleton: bool = True,
        replace: bool = False,
    ) -> None:
        return None


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


def test_portfolio_state_calculates_period_pnl_from_equity_observations() -> None:
    state = PortfolioState()
    first_day = datetime(2026, 10, 15, 12, tzinfo=UTC)
    next_day = first_day + timedelta(days=1)
    next_week = datetime(2026, 10, 19, 12, tzinfo=UTC)

    state.record_equity(first_day, 110_000.0)
    state.record_equity(next_day, 108_000.0)
    second_day_snapshot = state.snapshot()
    state.record_equity(next_week, 115_000.0)
    next_week_snapshot = state.snapshot()

    assert second_day_snapshot.daily_pnl == pytest.approx(-2_000.0)
    assert second_day_snapshot.weekly_pnl == pytest.approx(8_000.0)
    assert second_day_snapshot.monthly_pnl == pytest.approx(8_000.0)
    assert next_week_snapshot.daily_pnl == pytest.approx(7_000.0)
    assert next_week_snapshot.weekly_pnl == pytest.approx(7_000.0)
    assert next_week_snapshot.monthly_pnl == pytest.approx(15_000.0)


async def _started_plugin(
    *,
    config: Mapping[str, object] | None = None,
) -> tuple[PortfolioEnginePlugin, CapturingContext]:
    plugin = PortfolioEnginePlugin()
    context = CapturingContext(config=config)
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    context.published.clear()
    return plugin, context


def test_portfolio_engine_metadata_is_versioned() -> None:
    plugin = PortfolioEnginePlugin()

    assert plugin.metadata.plugin_id == "portfolio_engine"
    assert plugin.metadata.name == "Portfolio Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_portfolio_engine_installs_published_event_contracts() -> None:
    plugin = PortfolioEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_portfolio_engine_subscribes_to_trade_and_risk_events() -> None:
    plugin = PortfolioEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {
        PAPER_PORTFOLIO_UPDATED,
        PAPER_TRADE_CLOSED,
        TRADE_OPENED,
        RISK_APPROVED,
    }


async def test_portfolio_engine_publishes_required_event_methods() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_portfolio_updated(
        PortfolioUpdatedPayload(
            portfolio_id="primary",
            occurred_at=timestamp,
            capital=100_000.0,
            cash=98_000.0,
            open_positions=({"trade_id": "trade-1", "symbol": "AAPL", "quantity": 10.0},),
            closed_positions=(),
            exposure=2_000.0,
            sector_exposure={"Technology": 1.0},
            correlation=1.0,
            daily_pnl=0.0,
            weekly_pnl=0.0,
            monthly_pnl=0.0,
            drawdown=0.0,
            portfolio_risk=0.05,
        )
    )
    await plugin.publish_exposure_changed(
        ExposureChangedPayload(
            portfolio_id="primary",
            occurred_at=timestamp,
            exposure=2_000.0,
            previous_exposure=0.0,
            sector_exposure={"Technology": 1.0},
        )
    )
    await plugin.publish_drawdown_changed(
        DrawdownChangedPayload(
            portfolio_id="primary",
            occurred_at=timestamp,
            drawdown=0.01,
            previous_drawdown=0.0,
            peak_capital=101_000.0,
            capital=99_990.0,
        )
    )
    await plugin.publish_capital_updated(
        CapitalUpdatedPayload(
            portfolio_id="primary",
            occurred_at=timestamp,
            capital=100_000.0,
            previous_capital=99_000.0,
            cash=98_000.0,
            daily_pnl=1_000.0,
            weekly_pnl=1_000.0,
            monthly_pnl=1_000.0,
        )
    )

    assert [event.event_type for event in context.published] == [
        PORTFOLIO_UPDATED,
        EXPOSURE_CHANGED,
        DRAWDOWN_CHANGED,
        CAPITAL_UPDATED,
    ]
    assert all(event.source == "portfolio_engine" for event in context.published)


async def test_portfolio_engine_tracks_capital_positions_exposure_pnl_drawdown_and_risk() -> None:
    plugin, context = await _started_plugin(config={"initial_capital": 100_000.0})

    await plugin.handle_risk_approved(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "evidence": {"risk_amount": 500.0},
            },
        )
    )
    open_snapshot = {
        "initial_cash": 100_000.0,
        "cash": 98_200.0,
        "equity": 100_000.0,
        "peak_equity": 100_000.0,
        "virtual_positions": ({
            "trade_id": "trade-1", "symbol": "AAPL", "quantity": 10.0,
            "average_price": 180.0, "last_price": 180.0, "realized_pnl": 0.0,
            "sector": "Technology", "correlation_group": "MegaCapTech",
        },),
        "trade_history": (),
    }
    await plugin.handle_paper_portfolio_updated(EventEnvelope.create(
        source="paper_trading_engine", event_type=PAPER_PORTFOLIO_UPDATED,
        timestamp=_now(), payload=open_snapshot,
    ))
    await plugin.handle_trade_opened(EventEnvelope.create(
        source="paper_trading_engine",
        event_type=TRADE_OPENED,
        payload={"trade_id": "trade-1", "symbol": "AAPL", "quantity": 10.0, "entry_price": 180.0},
    ))
    opened = plugin.current_snapshot()
    modified_snapshot = {
        **open_snapshot,
        "cash": 97_820.0,
        "equity": 100_100.0,
        "peak_equity": 100_100.0,
        "virtual_positions": ({
            "trade_id": "trade-1", "symbol": "AAPL", "quantity": 12.0,
            "average_price": 181.6666666667, "last_price": 190.0, "realized_pnl": 0.0,
            "sector": "Technology", "correlation_group": "MegaCapTech",
        },),
    }
    await plugin.handle_paper_portfolio_updated(EventEnvelope.create(
        source="paper_trading_engine", event_type=PAPER_PORTFOLIO_UPDATED,
        timestamp=_now() + timedelta(minutes=1), payload=modified_snapshot,
    ))
    modified = plugin.current_snapshot()
    closed_snapshot = {
        **modified_snapshot,
        "cash": 100_040.0,
        "equity": 100_040.0,
        "virtual_positions": (),
        "trade_history": ({
            "trade_id": "trade-1", "symbol": "AAPL", "quantity": 12.0,
            "average_price": 181.6666666667, "exit_price": 185.0,
            "realized_pnl": 40.0,
        },),
    }
    await plugin.handle_paper_portfolio_updated(EventEnvelope.create(
        source="paper_trading_engine", event_type=PAPER_PORTFOLIO_UPDATED,
        timestamp=_now() + timedelta(minutes=2), payload=closed_snapshot,
    ))
    await plugin.handle_paper_trade_closed(EventEnvelope.create(
        source="paper_trading_engine", event_type=PAPER_TRADE_CLOSED,
        payload={"trade_id": "trade-1", "symbol": "AAPL"},
    ))
    closed = plugin.current_snapshot()

    assert opened.cash == 98_200.0
    assert opened.exposure == 1_800.0
    assert opened.sector_exposure == {"Technology": 1.0}
    assert opened.correlation == 1.0
    assert modified.capital == 100_100.0
    assert modified.daily_pnl == pytest.approx(100.0)
    assert modified.portfolio_risk > 0
    assert closed.open_positions == ()
    assert len(closed.closed_positions) == 1
    assert closed.daily_pnl == pytest.approx(40.0)
    assert closed.drawdown > 0
    assert {event.event_type for event in context.published} >= {
        PORTFOLIO_UPDATED,
        EXPOSURE_CHANGED,
        DRAWDOWN_CHANGED,
        CAPITAL_UPDATED,
    }


async def test_portfolio_engine_integrates_with_event_bus() -> None:
    plugin = PortfolioEnginePlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader([plugin]),
        plugin_config={"portfolio_engine": {"initial_capital": 50_000.0}},
    )
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    await kernel.event_bus.subscribe(PORTFOLIO_UPDATED, collect)
    await kernel.event_bus.subscribe(EXPOSURE_CHANGED, collect)
    await kernel.event_bus.subscribe(CAPITAL_UPDATED, collect)
    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="trade_manager",
            event_type=PAPER_PORTFOLIO_UPDATED,
            payload={
                "initial_cash": 50_000.0,
                "cash": 49_500.0,
                "equity": 50_000.0,
                "peak_equity": 50_000.0,
                "virtual_positions": ({
                    "trade_id": "trade-1", "symbol": "AAPL", "quantity": 5.0,
                    "average_price": 100.0, "last_price": 100.0,
                },),
                "trade_history": (),
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("portfolio_engine")
    await kernel.stop()

    assert {event.event_type for event in published} >= {
        PORTFOLIO_UPDATED,
        EXPOSURE_CHANGED,
        CAPITAL_UPDATED,
    }
    assert health.status is HealthStatus.HEALTHY
    assert health.details["open_positions"] == 1


def test_portfolio_payload_validates_drawdown_ratio() -> None:
    payload = DrawdownChangedPayload(
        portfolio_id="primary",
        occurred_at=_now(),
        drawdown=1.2,
        previous_drawdown=0.0,
        peak_capital=100_000.0,
        capital=90_000.0,
    )

    with pytest.raises(ValueError, match="drawdown"):
        payload.to_payload()


def test_portfolio_payloads_do_not_include_trading_signal_fields() -> None:
    payload = PortfolioUpdatedPayload(
        portfolio_id="primary",
        occurred_at=_now(),
        capital=100_000.0,
        cash=100_000.0,
        open_positions=(),
        closed_positions=(),
        exposure=0.0,
        sector_exposure={},
        correlation=0.0,
        daily_pnl=0.0,
        weekly_pnl=0.0,
        monthly_pnl=0.0,
        drawdown=0.0,
        portfolio_risk=0.0,
    ).to_payload()

    forbidden = {"candidate_type", "entry", "exit", "order", "recommendation", "signal", "side"}

    assert forbidden.isdisjoint(payload)
