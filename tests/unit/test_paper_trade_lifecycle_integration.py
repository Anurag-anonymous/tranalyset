from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.market_data import TICK_RECEIVED
from tranalyset.plugins.paper_trading_engine import (
    PAPER_EXECUTION_RECORDED,
    PAPER_PORTFOLIO_UPDATED,
    PAPER_RISK_LIMIT_ENFORCED,
    PaperTradingEnginePlugin,
)
from tranalyset.plugins.risk import STOP_LOSS_UPDATED, TRAILING_STOP_UPDATED
from tranalyset.plugins.portfolio_engine import PORTFOLIO_UPDATED, PortfolioEnginePlugin
from tranalyset.plugins.risk import RISK_APPROVED, RISK_REJECTED, TRADE_REQUESTED, RiskPlugin
from tranalyset.plugins.signal_orchestrator import ACCEPTED_CANDIDATE, SignalOrchestratorPlugin
from tranalyset.plugins.trade_manager import (
    TRADE_CLOSED,
    TRADE_MODIFIED,
    TRADE_OPENED,
    TRADE_SIGNAL_GENERATED,
    TradeManagerPlugin,
)

_NOW = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


async def _started_lifecycle(*, paper_config: dict[str, object] | None = None):
    signal_orchestrator = SignalOrchestratorPlugin()
    trade_manager = TradeManagerPlugin()
    risk = RiskPlugin()
    paper = PaperTradingEnginePlugin()
    portfolio = PortfolioEnginePlugin()
    kernel = Microkernel(
        plugin_loader=StaticPluginLoader(
            [signal_orchestrator, trade_manager, risk, paper, portfolio]
        ),
        plugin_config={
            "paper_trading_engine": {
                "initial_cash": 100_000.0,
                "commission_rate": 0.001,
                "slippage_rate": 0.001,
                **(paper_config or {}),
            }
        },
    )
    observed: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        observed.append(event)

    event_types = (
        ACCEPTED_CANDIDATE,
        TRADE_OPENED,
        TRADE_MODIFIED,
        TRADE_CLOSED,
        RISK_APPROVED,
        RISK_REJECTED,
        TRADE_REQUESTED,
        PAPER_EXECUTION_RECORDED,
        PAPER_PORTFOLIO_UPDATED,
        PAPER_RISK_LIMIT_ENFORCED,
        PORTFOLIO_UPDATED,
    )
    for event_type in event_types:
        await kernel.event_bus.subscribe(event_type, collect)
    await kernel.start()
    await kernel.event_bus.wait_until_idle()
    return kernel, signal_orchestrator, risk, paper, portfolio, observed


async def _publish_candidate(
    kernel: Microkernel,
    *,
    signal_id: str = "signal-1",
    symbol: str = "AAPL",
    confidence: float = 0.9,
) -> None:
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="momentum_strategy",
            event_type=TRADE_SIGNAL_GENERATED,
            timestamp=_NOW,
            payload={
                "candidate_type": "EntryCandidate",
                "signal_id": signal_id,
                "symbol": symbol,
                "timeframe": "1m",
                "generated_at": _NOW.isoformat(),
                "confidence": confidence,
                "reasoning": ("Validated momentum setup.",),
                "supporting_evidence": {
                    "entry_price": 100.0,
                    "sector": "Technology",
                    "correlation_group": "MegaCapTech",
                },
                "reward_risk_estimate": 2.0,
            },
        )
    )
    await kernel.event_bus.wait_until_idle()


async def _publish_tick(kernel: Microkernel, *, price: float, minute: int) -> None:
    timestamp = _NOW + timedelta(minutes=minute)
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="market_data",
            event_type=TICK_RECEIVED,
            timestamp=timestamp,
            payload={
                "symbol": "AAPL",
                "price": price,
                "size": 10_000.0,
                "venue": "integration",
                "received_at": timestamp.isoformat(),
                "side": "buy",
            },
        )
    )
    await kernel.event_bus.wait_until_idle()


@pytest.mark.asyncio
async def test_accepted_candidate_flows_through_fill_partial_exit_and_trade_ledger() -> None:
    kernel, _, _, paper, portfolio, observed = await _started_lifecycle()
    try:
        await _publish_candidate(kernel)
        trade_id = "trade:signal-1"
        position = paper.current_state().positions[trade_id]
        assert position.quantity > 0
        original_quantity = position.quantity
        assert any(event.event_type == ACCEPTED_CANDIDATE for event in observed)
        assert any(event.event_type == RISK_APPROVED for event in observed)
        assert any(event.event_type == TRADE_OPENED for event in observed)

        portfolio_snapshot = portfolio.current_snapshot()
        assert len(portfolio_snapshot.open_positions) == 1
        assert portfolio_snapshot.open_positions[0]["quantity"] == pytest.approx(position.quantity)
        assert portfolio_snapshot.capital == pytest.approx(paper.current_state().equity)

        partial_quantity = original_quantity / 2
        partial_exit = EventEnvelope.create(
            source="lifecycle_test",
            event_type=TRADE_MODIFIED,
            timestamp=_NOW + timedelta(minutes=1),
            payload={
                "trade_id": trade_id,
                "symbol": "AAPL",
                "modified_at": (_NOW + timedelta(minutes=1)).isoformat(),
                "changes": {
                    "partial_exit_quantity": partial_quantity,
                    "current_price": 105.0,
                    "status": "partially_closed",
                },
                "reason": "integration partial exit",
            },
        )
        await kernel.event_bus.publish(partial_exit)
        await kernel.event_bus.wait_until_idle()
        await kernel.event_bus.publish(partial_exit)
        await kernel.event_bus.wait_until_idle()

        remaining = paper.current_state().positions[trade_id]
        assert remaining.quantity == pytest.approx(original_quantity - partial_quantity)
        assert len(paper.current_state().trade_history) == 0
        assert paper.current_state().commissions > 0
        assert paper.current_state().slippage > 0

        await kernel.event_bus.publish(
            EventEnvelope.create(
                source="lifecycle_test",
                event_type=TRADE_CLOSED,
                timestamp=_NOW + timedelta(minutes=2),
                payload={
                    "trade_id": trade_id,
                    "symbol": "AAPL",
                    "closed_at": (_NOW + timedelta(minutes=2)).isoformat(),
                    "reason": "integration final exit",
                    "exit_price": 105.0,
                },
            )
        )
        await kernel.event_bus.wait_until_idle()

        state = paper.current_state()
        assert trade_id not in state.positions
        assert len(state.trade_history) == 1
        assert state.trade_history[0]["quantity"] == pytest.approx(original_quantity)
        assert state.trade_history[0]["realized_pnl"] > 0
        assert portfolio.current_snapshot().open_positions == ()
        assert portfolio.current_snapshot().closed_positions[0]["trade_id"] == trade_id
    finally:
        await kernel.stop()


@pytest.mark.asyncio
async def test_risk_rejection_prevents_paper_order_submission() -> None:
    kernel, _, _, paper, _, observed = await _started_lifecycle()
    try:
        await kernel.event_bus.publish(
            EventEnvelope.create(
                source="portfolio_test",
                event_type=PORTFOLIO_UPDATED,
                timestamp=_NOW,
                payload={
                    "capital": 100_000.0,
                    "exposure": 0.0,
                    "drawdown": 0.3,
                    "daily_pnl": 0.0,
                    "weekly_pnl": 0.0,
                    "monthly_pnl": 0.0,
                    "correlation": 0.0,
                },
            )
        )
        await kernel.event_bus.wait_until_idle()
        await _publish_candidate(kernel)

        assert any(event.event_type == RISK_REJECTED for event in observed)
        assert not any(event.event_type == RISK_APPROVED for event in observed)
        assert not any(
            event.payload.get("action") == "order_submitted"
            for event in observed
            if event.event_type == PAPER_EXECUTION_RECORDED
        )
        assert paper.current_state().orders == {}
        assert paper.current_state().positions == {}
    finally:
        await kernel.stop()


@pytest.mark.asyncio
async def test_second_candidate_for_market_is_not_submitted_while_trade_is_active() -> None:
    kernel, _, _, paper, _, observed = await _started_lifecycle()
    try:
        await _publish_candidate(kernel, signal_id="first-signal", confidence=0.6)
        await _publish_candidate(kernel, signal_id="later-better-signal", confidence=0.95)

        requests = [event for event in observed if event.event_type == TRADE_REQUESTED]
        assert len(requests) == 1
        assert requests[0].payload["signal_id"] == "first-signal"
        assert set(paper.current_state().positions) == {"trade:first-signal"}
    finally:
        await kernel.stop()


@pytest.mark.asyncio
async def test_forced_paper_drawdown_exit_reconciles_trade_and_portfolio_state() -> None:
    kernel, _, risk, paper, portfolio, observed = await _started_lifecycle(
        paper_config={
            "commission_rate": 0.0,
            "slippage_rate": 0.0,
            "max_drawdown": 0.001,
            "max_daily_loss": 0.99,
        }
    )
    try:
        await _publish_candidate(kernel)
        trade_id = "trade:signal-1"
        assert trade_id in paper.current_state().positions
        await _publish_tick(kernel, price=99.5, minute=1)

        assert trade_id not in paper.current_state().positions
        assert paper.current_state().trading_halted
        assert any(event.event_type == PAPER_RISK_LIMIT_ENFORCED for event in observed)
        assert paper.current_state().trade_history[0]["trade_id"] == trade_id
        assert trade_id in risk._closed_trades
        assert portfolio.current_snapshot().open_positions == ()
        assert portfolio.current_snapshot().closed_positions[0]["trade_id"] == trade_id
        assert portfolio.current_snapshot().portfolio_risk == 0.0
    finally:
        await kernel.stop()


@pytest.mark.asyncio
async def test_risk_rejects_new_requests_after_paper_trading_halts() -> None:
    kernel, _, risk, paper, _, observed = await _started_lifecycle(
        paper_config={
            "commission_rate": 0.0,
            "slippage_rate": 0.0,
            "max_drawdown": 0.001,
            "max_daily_loss": 0.99,
        }
    )
    try:
        await _publish_candidate(kernel)
        await _publish_tick(kernel, price=99.5, minute=1)
        assert paper.current_state().trading_halted
        assert risk._paper_halted
        previous_submissions = sum(
            event.payload.get("action") == "order_submitted"
            for event in observed
            if event.event_type == PAPER_EXECUTION_RECORDED
        )

        await _publish_candidate(kernel, signal_id="after-halt", symbol="MSFT")

        rejected = [
            event
            for event in observed
            if event.event_type == RISK_REJECTED
            and event.payload.get("trade_id") == "trade:after-halt"
        ]
        assert len(rejected) == 1
        assert rejected[0].payload["evidence"]["paper_trading_halted"] is True
        assert sum(
            event.payload.get("action") == "order_submitted"
            for event in observed
            if event.event_type == PAPER_EXECUTION_RECORDED
        ) == previous_submissions
        assert "trade:after-halt" not in paper.current_state().positions
    finally:
        await kernel.stop()


@pytest.mark.asyncio
async def test_stop_loss_executes_a_simulated_exit_and_updates_portfolio() -> None:
    kernel, _, _, paper, portfolio, observed = await _started_lifecycle(
        paper_config={"commission_rate": 0.0, "slippage_rate": 0.0}
    )
    try:
        await _publish_candidate(kernel)
        trade_id = "trade:signal-1"
        await kernel.event_bus.publish(
            EventEnvelope.create(
                source="risk",
                event_type=STOP_LOSS_UPDATED,
                timestamp=_NOW + timedelta(seconds=1),
                payload={"trade_id": trade_id, "symbol": "AAPL", "stop_loss_price": 98.0},
            )
        )
        await kernel.event_bus.wait_until_idle()
        await _publish_tick(kernel, price=97.0, minute=1)

        assert trade_id not in paper.current_state().positions
        assert paper.current_state().trade_history[0]["exit_price"] == pytest.approx(98.0)
        assert portfolio.current_snapshot().open_positions == ()
        assert portfolio.current_snapshot().closed_positions[0]["trade_id"] == trade_id
        assert any(event.event_type == TRADE_CLOSED for event in observed)
    finally:
        await kernel.stop()


@pytest.mark.asyncio
async def test_trailing_stop_executes_after_a_rally_and_reversal() -> None:
    kernel, _, _, paper, portfolio, _ = await _started_lifecycle(
        paper_config={"commission_rate": 0.0, "slippage_rate": 0.0}
    )
    try:
        await _publish_candidate(kernel)
        trade_id = "trade:signal-1"
        await kernel.event_bus.publish(
            EventEnvelope.create(
                source="risk",
                event_type=TRAILING_STOP_UPDATED,
                timestamp=_NOW + timedelta(seconds=1),
                payload={
                    "trade_id": trade_id,
                    "symbol": "AAPL",
                    "trailing_stop_price": 101.0,
                },
            )
        )
        await kernel.event_bus.wait_until_idle()
        await _publish_tick(kernel, price=102.0, minute=1)
        assert trade_id in paper.current_state().positions

        await _publish_tick(kernel, price=100.5, minute=2)

        assert trade_id not in paper.current_state().positions
        assert paper.current_state().trade_history[0]["exit_price"] == pytest.approx(101.0)
        assert portfolio.current_snapshot().open_positions == ()
        assert portfolio.current_snapshot().closed_positions[0]["trade_id"] == trade_id
    finally:
        await kernel.stop()
