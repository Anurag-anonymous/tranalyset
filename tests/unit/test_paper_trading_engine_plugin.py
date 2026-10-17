from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.market_data import CANDLE_CLOSED, TICK_RECEIVED
from tranalyset.plugins.paper_trading_engine import (
    PAPER_PORTFOLIO_UPDATED,
    PAPER_EXECUTION_RECORDED,
    PAPER_PERFORMANCE_REPORTED,
    PAPER_RISK_LIMIT_ENFORCED,
    PAPER_TRADE_CLOSED,
    PAPER_TRADE_UPDATED,
    PaperPortfolioUpdatedPayload,
    PaperTradeClosedPayload,
    PaperTradeUpdatedPayload,
    PaperTradingEnginePlugin,
)
from tranalyset.plugins.paper_trading_engine.contracts import PUBLISHED_EVENTS
from tranalyset.plugins.backtesting_engine.metrics import BacktestRecorder
from tranalyset.plugins.trade_manager import TRADE_CLOSED, TRADE_MODIFIED, TRADE_OPENED
from tranalyset.plugins.risk import STOP_LOSS_UPDATED, TAKE_PROFIT_UPDATED, TRAILING_STOP_UPDATED
from tranalyset.plugins.risk import RISK_APPROVED, RISK_REJECTED, TRADE_REQUESTED
from tranalyset.plugins.signal_orchestrator.contracts import ACCEPTED_CANDIDATE, REJECTED_CANDIDATE


class CapturingContext:
    def __init__(self, *, config: Mapping[str, object] | None = None) -> None:
        self.plugin_id = "paper_trading_engine"
        self.config = config or {}
        self.published: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published.append(event)

    def resolve(self, key: object) -> object:
        return key


class InstallContext:
    def __init__(self) -> None:
        self.plugin_id = "paper_trading_engine"
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


async def _started_plugin(
    *,
    config: Mapping[str, object] | None = None,
) -> tuple[PaperTradingEnginePlugin, CapturingContext]:
    plugin = PaperTradingEnginePlugin()
    context = CapturingContext(config=config)
    await plugin.install(InstallContext())
    await plugin.configure(context)
    await plugin.start(context)
    context.published.clear()
    return plugin, context


async def _approved_entry(
    plugin: PaperTradingEnginePlugin,
    *,
    trade_id: str,
    symbol: str,
    quantity: float,
    price: float,
    strategy_id: str | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    stop_price: float | None = None,
    timestamp: datetime | None = None,
) -> None:
    payload: dict[str, object] = {
        "trade_id": trade_id,
        "symbol": symbol,
        "entry_price": price,
        "quantity": quantity,
        "order_type": order_type,
        "source_event_type": str(TRADE_REQUESTED),
        "reason": "risk approved",
        "evidence": {"risk_amount": quantity},
    }
    if strategy_id is not None:
        payload["strategy_id"] = strategy_id
    if limit_price is not None:
        payload["limit_price"] = limit_price
    if stop_price is not None:
        payload["stop_price"] = stop_price
    await plugin.handle_risk_approved(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            timestamp=timestamp or _now(),
            payload=payload,
        )
    )


def test_paper_trading_engine_metadata_is_versioned() -> None:
    plugin = PaperTradingEnginePlugin()

    assert plugin.metadata.plugin_id == "paper_trading_engine"
    assert plugin.metadata.name == "Paper Trading Engine"
    assert plugin.metadata.version == "1.0.0"


async def test_paper_trading_engine_installs_published_event_contracts() -> None:
    plugin = PaperTradingEnginePlugin()
    install_context = InstallContext()

    await plugin.install(install_context)

    assert tuple(install_context.events) == PUBLISHED_EVENTS


def test_paper_trading_engine_subscribes_to_trade_and_market_events() -> None:
    plugin = PaperTradingEnginePlugin()

    subscriptions = plugin.subscriptions()

    assert set(subscriptions) == {
        TRADE_MODIFIED,
        TRADE_CLOSED,
        TICK_RECEIVED,
        CANDLE_CLOSED,
        STOP_LOSS_UPDATED,
        TAKE_PROFIT_UPDATED,
        ACCEPTED_CANDIDATE,
        REJECTED_CANDIDATE,
        RISK_APPROVED,
        RISK_REJECTED,
        TRAILING_STOP_UPDATED,
    }


async def test_paper_trading_engine_publishes_required_event_methods() -> None:
    plugin, context = await _started_plugin()
    timestamp = _now()

    await plugin.publish_paper_trade_updated(
        PaperTradeUpdatedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            occurred_at=timestamp,
            status="opened",
            quantity=10.0,
            average_price=100.0,
            last_price=101.0,
            realized_pnl=0.0,
            unrealized_pnl=10.0,
            commissions=1.0,
            slippage=0.05,
            order_type="market",
        )
    )
    await plugin.publish_paper_trade_closed(
        PaperTradeClosedPayload(
            trade_id="trade-1",
            symbol="AAPL",
            occurred_at=timestamp,
            quantity=10.0,
            average_price=100.0,
            exit_price=101.0,
            realized_pnl=9.0,
            commissions=2.0,
            slippage=0.1,
            reason="simulation close",
        )
    )
    await plugin.publish_paper_portfolio_updated(
        PaperPortfolioUpdatedPayload(
            portfolio_id="paper",
            occurred_at=timestamp,
            cash=100_000.0,
            equity=100_000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            commissions=0.0,
            slippage=0.0,
            virtual_positions=(),
            virtual_orders=(),
            trade_history=(),
        )
    )

    assert [event.event_type for event in context.published] == [
        PAPER_TRADE_UPDATED,
        PAPER_TRADE_CLOSED,
        PAPER_PORTFOLIO_UPDATED,
    ]
    assert all(event.source == "paper_trading_engine" for event in context.published)


async def test_market_order_opens_marks_and_closes_with_commissions_and_slippage() -> None:
    plugin, context = await _started_plugin(
        config={"initial_cash": 10_000.0, "commission_rate": 0.001, "slippage_rate": 0.001}
    )

    await _approved_entry(
        plugin,
        trade_id="trade-1",
        strategy_id="strategy-a",
        symbol="AAPL",
        quantity=10.0,
        price=100.0,
    )
    await plugin.handle_tick_received(
        EventEnvelope.create(
            source="market_data",
            event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "price": 105.0, "size": 1.0},
        )
    )
    await plugin.handle_trade_closed(
        EventEnvelope.create(
            source="trade_manager",
            event_type=TRADE_CLOSED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "exit_price": 106.0,
                "reason": "simulation complete",
            },
        )
    )

    state = plugin.current_state()
    assert state.positions == {}
    assert len(state.trade_history) == 1
    assert state.trade_history[0]["realized_pnl"] == pytest.approx(state.equity - state.initial_cash)
    assert state.commissions > 0
    assert state.trade_history[0]["slippage"] == pytest.approx(2.06)
    closed_event = next(event for event in context.published if event.event_type == PAPER_TRADE_CLOSED)
    assert closed_event.payload["realized_pnl"] == pytest.approx(state.trade_history[0]["realized_pnl"])
    assert closed_event.payload["strategy_id"] == "strategy-a"
    recorder = BacktestRecorder(initial_capital=state.initial_cash)
    for event in context.published:
        recorder.record(event)
    result = recorder.build_result(
        run_id="fees-slippage",
        strategy_id="strategy-a",
        mode="historical_replay",
        started_at=min(event.timestamp for event in context.published),
        completed_at=max(event.timestamp for event in context.published),
    )
    assert result.status == "evaluated"
    assert result.trades[0].realized_pnl == pytest.approx(state.equity - state.initial_cash)
    assert result.metadata["execution_assumptions"]["observed_fees"] == state.commissions
    assert {event.event_type for event in context.published} >= {
        PAPER_TRADE_UPDATED,
        PAPER_TRADE_CLOSED,
        PAPER_PORTFOLIO_UPDATED,
    }


async def test_limit_and_stop_orders_fill_from_market_data() -> None:
    plugin, _ = await _started_plugin(
        config={"initial_cash": 10_000.0, "commission_rate": 0.0, "slippage_rate": 0.0}
    )

    await _approved_entry(
        plugin, trade_id="limit-1", symbol="AAPL", quantity=5.0,
        price=100.0, order_type="limit", limit_price=99.0,
    )
    await _approved_entry(
        plugin, trade_id="stop-1", symbol="MSFT", quantity=5.0,
        price=100.0, order_type="stop", stop_price=110.0,
    )
    await plugin.handle_tick_received(
        EventEnvelope.create(
            source="market_data",
            event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "price": 98.5, "size": 1.0},
        )
    )
    await plugin.handle_candle_closed(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={"symbol": "MSFT", "close_price": 111.0},
        )
    )

    state = plugin.current_state()
    assert set(state.positions) == {"limit-1", "stop-1"}
    assert state.positions["limit-1"].average_price == 99.0
    assert state.positions["stop-1"].average_price == 110.0


async def test_partial_exit_and_trailing_stop_are_simulated() -> None:
    plugin, context = await _started_plugin(
        config={"initial_cash": 10_000.0, "commission_rate": 0.0, "slippage_rate": 0.0}
    )

    await _approved_entry(plugin, trade_id="trade-1", symbol="AAPL", quantity=10.0, price=100.0)
    await plugin.handle_trade_modified(
        EventEnvelope.create(
            source="trade_manager",
            event_type=TRADE_MODIFIED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "changes": {
                    "partial_exit_quantity": 4.0,
                    "current_price": 105.0,
                    "trailing_distance": 2.0,
                },
            },
        )
    )
    await plugin.handle_tick_received(
        EventEnvelope.create(
            source="market_data",
            event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "price": 110.0, "size": 1.0},
        )
    )
    await plugin.handle_tick_received(
        EventEnvelope.create(
            source="market_data",
            event_type=TICK_RECEIVED,
            payload={"symbol": "AAPL", "price": 108.0, "size": 1.0},
        )
    )

    state = plugin.current_state()
    assert "trade-1" not in state.positions
    assert len(state.trade_history) == 1
    assert state.trade_history[0]["realized_pnl"] == 68.0
    closed_event = next(event for event in context.published if event.event_type == PAPER_TRADE_CLOSED)
    assert closed_event.payload["quantity"] == 10.0
    recorder = BacktestRecorder(initial_capital=state.initial_cash)
    for event in context.published:
        recorder.record(event)
    result = recorder.build_result(
        run_id="partial-exits",
        strategy_id="strategy-a",
        mode="historical_replay",
        started_at=min(event.timestamp for event in context.published),
        completed_at=max(event.timestamp for event in context.published),
    )
    assert result.status == "evaluated"
    assert result.metrics_payload()["trade_count"] == 1
    assert result.trades[0].realized_pnl == 68.0


async def test_trailing_stop_exit_respects_adverse_candle_gap() -> None:
    plugin, _ = await _started_plugin(
        config={"initial_cash": 10_000.0, "commission_rate": 0.0, "slippage_rate": 0.0}
    )
    await _approved_entry(
        plugin,
        trade_id="gap-stop",
        symbol="AAPL",
        quantity=10.0,
        price=100.0,
    )
    await plugin.handle_trailing_stop_updated(
        EventEnvelope.create(
            source="risk",
            event_type=TRAILING_STOP_UPDATED,
            payload={
                "trade_id": "gap-stop",
                "symbol": "AAPL",
                "trailing_stop_price": 98.0,
            },
        )
    )
    await plugin.handle_candle_closed(
        EventEnvelope.create(
            source="market_data",
            event_type=CANDLE_CLOSED,
            payload={
                "symbol": "AAPL",
                "open_price": 95.0,
                "close_price": 97.0,
            },
        )
    )

    state = plugin.current_state()
    assert "gap-stop" not in state.positions
    assert state.trade_history[0]["exit_price"] == pytest.approx(95.0)


async def test_entry_affordability_includes_slippage_and_commission() -> None:
    plugin, _ = await _started_plugin(
        config={"initial_cash": 10_000.0, "commission_rate": 0.001, "slippage_rate": 0.001}
    )

    await _approved_entry(
        plugin,
        trade_id="exact-cash",
        symbol="AAPL",
        quantity=100.0,
        price=100.0,
    )

    state = plugin.current_state()
    assert state.positions == {}
    assert state.cash == pytest.approx(10_000.0)
    assert any(item["event_type"] == "fill_rejected" for item in state.event_history)


async def test_paper_trading_engine_integrates_with_event_bus() -> None:
    plugin = PaperTradingEnginePlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader([plugin]),
        plugin_config={
            "paper_trading_engine": {
                "initial_cash": 10_000.0,
                "commission_rate": 0.0,
                "slippage_rate": 0.0,
            }
        },
    )
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    await kernel.event_bus.subscribe(PAPER_TRADE_UPDATED, collect)
    await kernel.event_bus.subscribe(PAPER_PORTFOLIO_UPDATED, collect)
    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "quantity": 2.0,
                "entry_price": 100.0,
                "order_type": "market",
                "source_event_type": str(TRADE_REQUESTED),
                "reason": "risk approved",
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    health = await kernel.plugin_health("paper_trading_engine")
    await kernel.stop()

    assert {event.event_type for event in published} >= {PAPER_TRADE_UPDATED, PAPER_PORTFOLIO_UPDATED}
    assert health.status is HealthStatus.HEALTHY
    assert health.details["virtual_positions"] == 1


def test_paper_payloads_do_not_include_live_broker_fields() -> None:
    payload = PaperPortfolioUpdatedPayload(
        portfolio_id="paper",
        occurred_at=_now(),
        cash=100_000.0,
        equity=100_000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        commissions=0.0,
        slippage=0.0,
        virtual_positions=(),
        virtual_orders=(),
        trade_history=(),
    ).to_payload()

    forbidden = {"broker_order_id", "live_order", "api_key", "broker_account"}

    assert forbidden.isdisjoint(payload)


async def test_paper_execution_applies_latency_liquidity_gaps_and_records_a_reproducible_journal() -> None:
    plugin, context = await _started_plugin(config={"commission_rate": 0.0, "slippage_rate": 0.0, "latency_ms": 1000, "liquidity_constrained": True, "max_order_participation": 0.5})
    now = _now()
    await _approved_entry(plugin, trade_id="limit", symbol="AAPL", quantity=4.0, price=100.0, order_type="limit", limit_price=100.0, timestamp=now)
    await plugin.handle_tick_received(EventEnvelope.create(source="market_data", event_type=TICK_RECEIVED, timestamp=now + timedelta(milliseconds=500), payload={"symbol": "AAPL", "price": 90.0, "size": 4.0}))
    assert plugin.current_state().positions == {}
    await plugin.handle_candle_closed(EventEnvelope.create(source="market_data", event_type=CANDLE_CLOSED, timestamp=now + timedelta(seconds=1), payload={"symbol": "AAPL", "open_price": 90.0, "close_price": 95.0, "volume": 4.0}))

    state = plugin.current_state()
    assert state.positions["limit"].quantity == 2.0
    assert state.positions["limit"].average_price == 90.0
    assert any(item["event_type"] == "partial_fill" for item in plugin.event_history())
    assert any(event.event_type == PAPER_EXECUTION_RECORDED for event in context.published)


async def test_paper_risk_limits_halt_new_orders_and_performance_reports_are_emitted() -> None:
    plugin, context = await _started_plugin(config={"initial_cash": 1_000.0, "commission_rate": 0.0, "slippage_rate": 0.0, "max_exposure": 0.1})
    await _approved_entry(plugin, trade_id="too-large", symbol="AAPL", quantity=2.0, price=100.0)

    assert plugin.current_state().positions == {}
    assert any(item["event_type"] == "fill_rejected" for item in plugin.event_history())
    assert any(event.event_type == PAPER_RISK_LIMIT_ENFORCED for event in context.published)
    assert {event.event_type for event in context.published} >= {PAPER_PERFORMANCE_REPORTED}


async def test_paper_journal_records_signals_and_risk_decisions() -> None:
    plugin, _ = await _started_plugin()
    timestamp = _now()
    for event_type in (ACCEPTED_CANDIDATE, REJECTED_CANDIDATE, RISK_APPROVED, RISK_REJECTED):
        payload: dict[str, object] = {"symbol": "AAPL", "trade_id": "trade-1", "reason": "fixture"}
        if event_type == RISK_APPROVED:
            payload.update({
                "source_event_type": str(TRADE_REQUESTED),
                "entry_price": 100.0,
                "quantity": 1.0,
                "order_type": "market",
            })
        event = EventEnvelope.create(source="upstream", event_type=event_type, timestamp=timestamp, payload=payload)
        handler = plugin.subscriptions()[event_type][0]
        await handler(event)

    assert "risk_approved" in [item["event_type"] for item in plugin.event_history()]
    assert "risk_rejected" in [item["event_type"] for item in plugin.event_history()]
