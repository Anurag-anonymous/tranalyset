from datetime import UTC, datetime

import pytest

from tranalyset.domain.common import OrderSide, OrderStatus, OrderType
from tranalyset.domain.common.value_objects import Price, Quantity
from tranalyset.domain.market.models import Instrument
from tranalyset.domain.market.models import Exchange, Symbol
from tranalyset.domain.common import AssetClass
from tranalyset.domain.trading import (
    Commission,
    Confidence,
    Entry,
    Execution,
    Exit,
    Order,
    RewardRisk,
    Slippage,
    Trade,
    TradeDirection,
    TradeEvidence,
    TradeHealth,
    TradeId,
    TradeMetrics,
    TradeReason,
    TradeStatus,
    TradingDomainError,
)


@pytest.fixture
def aapl_instrument() -> Instrument:
    return Instrument(
        symbol=Symbol("AAPL"),
        exchange=Exchange("NASDAQ"),
        asset_class=AssetClass.EQUITY,
    )


def _now() -> datetime:
    return datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


def test_trade_id_is_immutable_and_hashable() -> None:
    trade_id = TradeId("trade-1")

    assert trade_id == "trade-1"
    assert hash(trade_id) == hash(TradeId("trade-1"))
    with pytest.raises(AttributeError):
        trade_id.value = "trade-2"  # type: ignore[misc]


def test_trade_evidence_is_immutable() -> None:
    evidence = TradeEvidence(data={"trend": "confirmed"}, sources=("market_state",))

    assert evidence.data["trend"] == "confirmed"
    assert hash(evidence)
    with pytest.raises(TypeError):
        evidence.data["trend"] = "weakened"  # type: ignore[index]


def test_trade_health_and_metrics_validate_ranges() -> None:
    metrics = TradeMetrics(
        confidence=Confidence(0.8),
        reward_risk=RewardRisk(2.5),
        evidence=TradeEvidence(data={"health": "good"}),
    )
    health = TradeHealth(
        trade_id="trade-1",
        score=82,
        confidence=Confidence(0.8),
        reward_risk=RewardRisk(2.5),
        status=TradeStatus.OPEN,
        reason=TradeReason("Health is acceptable"),
        evidence=TradeEvidence(data={"trend": "strong"}),
    )

    assert metrics.confidence == Confidence(0.8)
    assert health.score == 82

    with pytest.raises(ValueError, match="Confidence"):
        Confidence(1.2)
    with pytest.raises(ValueError, match="RewardRisk"):
        RewardRisk(0)
    with pytest.raises(ValueError, match="score"):
        TradeHealth(
            trade_id="trade-1",
            score=101,
            confidence=Confidence(0.8),
            reward_risk=RewardRisk(2.5),
        )


def test_order_transitions_are_validated(aapl_instrument: Instrument) -> None:
    order = Order(
        order_id="order-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Quantity(10),
    )

    submitted = order.submit()
    filled = submitted.fill()

    assert submitted.status == OrderStatus.SUBMITTED
    assert filled.status == OrderStatus.FILLED

    with pytest.raises(TradingDomainError, match="Illegal order transition"):
        order.fill()


def test_trade_lifecycle_transitions_are_validated(aapl_instrument: Instrument) -> None:
    trade = Trade(
        trade_id="trade-1",
        order_id="order-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        quantity=Quantity(10),
        price=Price(100),
        executed_at=_now(),
        evidence=TradeEvidence(data={"source": "signal_engine"}),
    )
    entry = Entry(
        trade_id="trade-1",
        price=Price(100),
        quantity=Quantity(10),
        entered_at=_now(),
        reason=TradeReason("Signal aligned"),
    )
    exit = Exit(
        trade_id="trade-1",
        price=Price(110),
        quantity=Quantity(10),
        exited_at=_now(),
        reason=TradeReason("Target reached"),
    )

    opened = trade.open(entry)
    partially_closed = opened.partially_close(
        Exit(
            trade_id="trade-1",
            price=Price(105),
            quantity=Quantity(4),
            exited_at=_now(),
        )
    )
    closed = partially_closed.close(exit)

    assert opened.status == TradeStatus.OPEN
    assert partially_closed.status == TradeStatus.PARTIALLY_CLOSED
    assert closed.status == TradeStatus.CLOSED
    assert closed.trade_id == "trade-1"
    assert closed.direction == TradeDirection.LONG
    assert closed.entry == entry
    assert closed.exit == exit

    with pytest.raises(TradingDomainError, match="Illegal trade transition"):
        trade.close(exit)


def test_execution_commission_slippage_and_reasoning_objects_are_immutable(
    aapl_instrument: Instrument,
) -> None:
    execution = Execution(
        execution_id="exec-1",
        trade_id="trade-1",
        order_id="order-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        quantity=Quantity(10),
        price=Price(100),
        executed_at=_now(),
        commission=Commission(Price(1)),
        slippage=Slippage(Price(0.25)),
    )

    assert execution.trade_id == "trade-1"
    assert execution.notional == 1000.0
    assert execution.commission.amount == Price(1)
    assert execution.slippage.amount == Price(0.25)


def test_trade_domain_model_exports_are_immutable(aapl_instrument: Instrument) -> None:
    trade = Trade(
        trade_id="trade-1",
        order_id="order-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        quantity=Quantity(10),
        price=Price(100),
        executed_at=_now(),
        metrics=TradeMetrics(
            confidence=Confidence(0.7),
            reward_risk=RewardRisk(2.0),
            evidence=TradeEvidence(data={"trend": "strong"}),
        ),
    )

    assert trade.status == TradeStatus.PROPOSED
    assert trade.metrics is not None
    with pytest.raises(AttributeError):
        trade.status = TradeStatus.OPEN  # type: ignore[misc]
