"""Unit tests for the Domain Layer bounded context."""
import ast
import inspect
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
import pytest

from tranalyset.domain.common import AssetClass, OrderSide, OrderType, OrderStatus
from tranalyset.domain.common.value_objects import (
    Currency,
    Liquidity,
    Money,
    Price,
    Quantity,
    Ratio,
    Spread,
    Symbol,
    Exchange,
    Volume,
)
from tranalyset.domain.market.models import Candle, Instrument, MarketLiquidity, OrderBook, PriceQuote, Quote, Tick, Timeframe
from tranalyset.domain.trading.models import Order, Trade
from tranalyset.domain.portfolio.models import Portfolio, Position
from tranalyset.domain.strategy.models import Signal


@pytest.fixture
def aapl_instrument() -> Instrument:
    return Instrument(
        symbol=Symbol("AAPL"),
        exchange=Exchange("NASDAQ"),
        asset_class=AssetClass.EQUITY,
    )


# === Instrument Tests ===

def test_instrument_creation(aapl_instrument: Instrument) -> None:
    assert aapl_instrument.symbol == Symbol("AAPL")
    assert aapl_instrument.exchange == Exchange("NASDAQ")
    assert aapl_instrument.asset_class == AssetClass.EQUITY
    assert aapl_instrument.pip_size == 0.01
    assert aapl_instrument.multiplier == 1.0


def test_instrument_equality(aapl_instrument: Instrument) -> None:
    same = Instrument(
        symbol=Symbol("AAPL"),
        exchange=Exchange("NASDAQ"),
        asset_class=AssetClass.EQUITY,
    )
    assert aapl_instrument == same
    assert hash(aapl_instrument) == hash(same)


def test_instrument_inequality(aapl_instrument: Instrument) -> None:
    different = Instrument(
        symbol=Symbol("GOOG"),
        exchange=Exchange("NASDAQ"),
        asset_class=AssetClass.EQUITY,
    )
    assert aapl_instrument != different


def test_instrument_validation_pip_size() -> None:
    with pytest.raises(ValueError, match="Pip size"):
        Instrument(
            symbol=Symbol("AAPL"),
            exchange=Exchange("NASDAQ"),
            asset_class=AssetClass.EQUITY,
            pip_size=0.0,
        )


def test_instrument_validation_multiplier() -> None:
    with pytest.raises(ValueError, match="Multiplier"):
        Instrument(
            symbol=Symbol("AAPL"),
            exchange=Exchange("NASDAQ"),
            asset_class=AssetClass.EQUITY,
            multiplier=0.0,
        )


# === Value Object Tests ===

def test_symbol_creation() -> None:
    s = Symbol("AAPL")
    assert s.value == "AAPL"
    assert str(s) == "AAPL"


def test_symbol_empty_validation() -> None:
    with pytest.raises(ValueError, match="empty"):
        Symbol("")


def test_symbol_equality() -> None:
    assert Symbol("AAPL") == Symbol("AAPL")
    assert Symbol("AAPL") != Symbol("GOOG")
    assert hash(Symbol("AAPL")) == hash(Symbol("AAPL"))


def test_exchange_creation() -> None:
    e = Exchange("NASDAQ")
    assert e.value == "NASDAQ"
    assert str(e) == "NASDAQ"


def test_exchange_empty_validation() -> None:
    with pytest.raises(ValueError, match="empty"):
        Exchange("")


def test_exchange_equality() -> None:
    assert Exchange("NASDAQ") == Exchange("NASDAQ")
    assert Exchange("NASDAQ") != Exchange("NYSE")
    assert hash(Exchange("NASDAQ")) == hash(Exchange("NASDAQ"))


def test_quantity_creation() -> None:
    q = Quantity(100.0)
    assert q.value == 100.0
    assert str(q) == "100.0"


def test_quantity_validation() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        Quantity(0)
    with pytest.raises(ValueError, match="greater than zero"):
        Quantity(-10.0)


def test_quantity_equality() -> None:
    assert Quantity(100.0) == Quantity(100.0)
    assert Quantity(100.0) != Quantity(50.0)
    assert hash(Quantity(100.0)) == hash(Quantity(100.0))


def test_price_creation() -> None:
    p = Price(100.0)
    assert p.value == 100.0
    assert str(p) == "100.0"


def test_price_validation() -> None:
    with pytest.raises(ValueError, match="negative"):
        Price(-10.0)


def test_price_equality() -> None:
    assert Price(100.0) == Price(100.0)
    assert Price(100.0) != Price(50.0)
    assert hash(Price(100.0)) == hash(Price(100.0))


def test_volume_spread_and_liquidity_creation() -> None:
    volume = Volume(1234.5)
    spread = Spread(0.25)
    liquidity = Liquidity(2500)

    assert volume.value == 1234.5
    assert spread.value == 0.25
    assert liquidity.value == 2500.0
    assert hash(volume) == hash(Volume(1234.5))
    assert hash(spread) == hash(Spread(0.25))
    assert hash(liquidity) == hash(Liquidity(2500))


def test_volume_spread_and_liquidity_validation() -> None:
    with pytest.raises(ValueError, match="Volume"):
        Volume(-1)
    with pytest.raises(ValueError, match="Spread"):
        Spread(-0.1)
    with pytest.raises(ValueError, match="Liquidity"):
        Liquidity(-1)


# === PriceQuote Tests ===

def test_price_quote_creation(aapl_instrument: Instrument) -> None:
    quote = PriceQuote(
        instrument=aapl_instrument,
        timestamp=datetime.now(UTC),
        bid=Price(100.0),
        ask=Price(101.0),
        last=Price(100.5),
    )
    assert quote.instrument == aapl_instrument
    assert quote.bid == Price(100.0)
    assert quote.ask == Price(101.0)
    assert quote.last == Price(100.5)
    assert Quote is PriceQuote


def test_price_quote_bid_ask_validation(aapl_instrument: Instrument) -> None:
    with pytest.raises(ValueError, match="Bid price must not be greater"):
        PriceQuote(
            instrument=aapl_instrument,
            timestamp=datetime.now(UTC),
            bid=Price(102.0),
            ask=Price(101.0),
            last=Price(100.5),
        )


# === Order Tests ===

def test_order_creation(aapl_instrument: Instrument) -> None:
    order = Order(
        order_id="ord-123",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Quantity(100.0),
    )
    assert order.order_id == "ord-123"
    assert order.status == OrderStatus.PENDING
    assert order.price is None


def test_order_with_status(aapl_instrument: Instrument) -> None:
    order = Order(
        order_id="ord-123",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Quantity(100.0),
    )
    updated = order.with_status(OrderStatus.SUBMITTED)
    assert updated.status == OrderStatus.SUBMITTED
    assert order.status == OrderStatus.PENDING  # Immutability check


def test_order_empty_id_validation(aapl_instrument: Instrument) -> None:
    with pytest.raises(ValueError, match="Order ID"):
        Order(
            order_id="",
            instrument=aapl_instrument,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Quantity(100.0),
        )


def test_order_limit_price_validation(aapl_instrument: Instrument) -> None:
    with pytest.raises(ValueError, match="Limit order must have a valid positive price"):
        Order(
            order_id="ord-123",
            instrument=aapl_instrument,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Quantity(100.0),
            price=None,
        )


def test_order_limit_with_price(aapl_instrument: Instrument) -> None:
    order = Order(
        order_id="ord-123",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Quantity(100.0),
        price=Price(150.0),
    )
    assert order.price == Price(150.0)


# === Trade Tests ===

def test_trade_creation(aapl_instrument: Instrument) -> None:
    trade = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        quantity=Quantity(10.0),
        price=Price(100.0),
        executed_at=datetime.now(UTC),
    )
    assert trade.trade_id == "t-1"
    assert trade.fee == Price(0.0)


def test_trade_empty_id_validation(aapl_instrument: Instrument) -> None:
    with pytest.raises(ValueError, match="Trade ID"):
        Trade(
            trade_id="",
            order_id="o-1",
            instrument=aapl_instrument,
            side=OrderSide.BUY,
            quantity=Quantity(10.0),
            price=Price(100.0),
            executed_at=datetime.now(UTC),
        )


def test_trade_empty_order_id_validation(aapl_instrument: Instrument) -> None:
    with pytest.raises(ValueError, match="Order ID"):
        Trade(
            trade_id="t-1",
            order_id="",
            instrument=aapl_instrument,
            side=OrderSide.BUY,
            quantity=Quantity(10.0),
            price=Price(100.0),
            executed_at=datetime.now(UTC),
        )


# === Position Tests ===

def test_position_long_pnl(aapl_instrument: Instrument) -> None:
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
        current_price=105.0,
    )
    assert pos.direction == "long"
    assert pos.unrealized_pnl == 50.0  # (105 - 100) * 10


def test_position_short_pnl(aapl_instrument: Instrument) -> None:
    pos = Position(
        instrument=aapl_instrument,
        quantity=-10.0,
        average_entry_price=100.0,
        current_price=95.0,
    )
    assert pos.direction == "short"
    assert pos.unrealized_pnl == 50.0  # (100 - 95) * 10


def test_position_flat() -> None:
    instr = Instrument(
        symbol=Symbol("AAPL"),
        exchange=Exchange("NASDAQ"),
        asset_class=AssetClass.EQUITY,
    )
    pos = Position(
        instrument=instr,
        quantity=0.0,
        average_entry_price=100.0,
    )
    assert pos.direction == "flat"
    assert pos.unrealized_pnl == 0.0


def test_position_addition_same_direction(aapl_instrument: Instrument) -> None:
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
    )
    trade = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        quantity=Quantity(10.0),
        price=Price(110.0),
        executed_at=datetime.now(UTC),
        fee=Price(5.0),
    )
    new_pos = pos.add_trade(trade)
    assert new_pos.quantity == 20.0
    assert new_pos.average_entry_price == 105.0  # (10*100 + 10*110) / 20
    assert new_pos.realized_pnl == -5.0  # fee deducted


def test_position_reduction(aapl_instrument: Instrument) -> None:
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
    )
    trade = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=aapl_instrument,
        side=OrderSide.SELL,
        quantity=Quantity(5.0),
        price=Price(105.0),
        executed_at=datetime.now(UTC),
        fee=Price(2.0),
    )
    new_pos = pos.add_trade(trade)
    assert new_pos.quantity == 5.0
    assert new_pos.average_entry_price == 100.0
    assert new_pos.realized_pnl == 23.0  # (105-100)*5 - 2.0 = 23.0


def test_position_reversal(aapl_instrument: Instrument) -> None:
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
    )
    trade = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=aapl_instrument,
        side=OrderSide.SELL,
        quantity=Quantity(15.0),
        price=Price(105.0),
        executed_at=datetime.now(UTC),
        fee=Price(3.0),
    )
    new_pos = pos.add_trade(trade)
    assert new_pos.quantity == -5.0
    assert new_pos.average_entry_price == 105.0
    assert new_pos.realized_pnl == 47.0  # (105-100)*10 - 3.0 = 47.0


def test_position_close(aapl_instrument: Instrument) -> None:
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
    )
    trade = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=aapl_instrument,
        side=OrderSide.SELL,
        quantity=Quantity(10.0),
        price=Price(105.0),
        executed_at=datetime.now(UTC),
        fee=Price(5.0),
    )
    new_pos = pos.add_trade(trade)
    assert new_pos.quantity == 0.0
    assert new_pos.average_entry_price == 0.0
    assert new_pos.realized_pnl == 45.0  # (105-100)*10 - 5.0 = 45.0


def test_position_wrong_instrument(aapl_instrument: Instrument) -> None:
    goog = Instrument(
        symbol=Symbol("GOOG"),
        exchange=Exchange("NASDAQ"),
        asset_class=AssetClass.EQUITY,
    )
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
    )
    trade = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=goog,
        side=OrderSide.BUY,
        quantity=Quantity(10.0),
        price=Price(100.0),
        executed_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="Trade instrument does not match"):
        pos.add_trade(trade)


def test_position_update_market_price(aapl_instrument: Instrument) -> None:
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
        current_price=100.0,
    )
    updated = pos.update_market_price(110.0)
    assert updated.current_price == 110.0
    assert pos.current_price == 100.0  # Immutability check


# === Signal Tests ===

def test_signal_creation(aapl_instrument: Instrument) -> None:
    sig = Signal(
        signal_id="sig-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        strength=0.8,
        confidence=0.9,
        timestamp=datetime.now(UTC),
    )
    assert sig.strength == 0.8
    assert sig.confidence == 0.9


def test_signal_strength_validation(aapl_instrument: Instrument) -> None:
    with pytest.raises(ValueError, match="Strength"):
        Signal(
            signal_id="sig-1",
            instrument=aapl_instrument,
            side=OrderSide.BUY,
            strength=1.2,
            confidence=0.9,
            timestamp=datetime.now(UTC),
        )


def test_signal_confidence_validation(aapl_instrument: Instrument) -> None:
    with pytest.raises(ValueError, match="Confidence"):
        Signal(
            signal_id="sig-1",
            instrument=aapl_instrument,
            side=OrderSide.BUY,
            strength=0.8,
            confidence=-0.1,
            timestamp=datetime.now(UTC),
        )


# === Portfolio Tests ===

def test_portfolio_creation() -> None:
    portfolio = Portfolio(
        portfolio_id="p-1",
        account_id="acc-1",
        balance=10000.0,
    )
    assert portfolio.portfolio_id == "p-1"
    assert portfolio.balance == 10000.0
    assert portfolio.equity == 10000.0


def test_portfolio_apply_trade_and_valuation(aapl_instrument: Instrument) -> None:
    portfolio = Portfolio(
        portfolio_id="p-1",
        account_id="acc-1",
        balance=10000.0,
    )

    # 1. Buy 10 AAPL at 100.0
    trade1 = Trade(
        trade_id="t-1",
        order_id="o-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        quantity=Quantity(10.0),
        price=Price(100.0),
        executed_at=datetime.now(UTC),
        fee=Price(10.0),
    )
    portfolio = portfolio.apply_trade(trade1)
    assert portfolio.balance == 8990.0  # 10000 - 1000 - 10
    assert portfolio.equity == 8990.0  # Unrealized pnl is 0

    # 2. Update quote to 105.0 (Bid: 105.0, Ask: 106.0)
    quote = PriceQuote(
        instrument=aapl_instrument,
        timestamp=datetime.now(UTC),
        bid=Price(105.0),
        ask=Price(106.0),
        last=Price(105.5),
    )
    portfolio = portfolio.update_quote(quote)
    assert portfolio.total_unrealized_pnl == 50.0  # (105 - 100) * 10
    assert portfolio.equity == 9040.0  # 8990 + 50

    # 3. Sell 5 AAPL at 108.0
    trade2 = Trade(
        trade_id="t-2",
        order_id="o-2",
        instrument=aapl_instrument,
        side=OrderSide.SELL,
        quantity=Quantity(5.0),
        price=Price(108.0),
        executed_at=datetime.now(UTC),
        fee=Price(5.0),
    )
    portfolio = portfolio.apply_trade(trade2)
    assert portfolio.balance == 9525.0  # 8990 + 540 - 5


def test_portfolio_empty_id_validation() -> None:
    with pytest.raises(ValueError, match="Portfolio ID"):
        Portfolio(
            portfolio_id="",
            account_id="acc-1",
            balance=10000.0,
        )


def test_portfolio_negative_balance_validation() -> None:
    with pytest.raises(ValueError, match="Balance"):
        Portfolio(
            portfolio_id="p-1",
            account_id="acc-1",
            balance=-100.0,
        )


# === Analytics Model Tests ===

def test_market_metric_creation() -> None:
    from tranalyset.domain.analytics.models import MarketMetric
    instr = Instrument(
        symbol=Symbol("AAPL"),
        exchange=Exchange("NASDAQ"),
        asset_class=AssetClass.EQUITY,
    )
    metric = MarketMetric(
        instrument=instr,
        timestamp=datetime.now(UTC),
        value=150.0,
    )
    assert metric.instrument == instr
    assert metric.value == 150.0


# === Risk Model Tests ===

def test_risk_metric_creation(aapl_instrument: Instrument) -> None:
    from tranalyset.domain.risk.models import RiskMetric, Exposure
    pos = Position(
        instrument=aapl_instrument,
        quantity=10.0,
        average_entry_price=100.0,
        current_price=105.0,
    )
    metric = RiskMetric(position=pos, value=0.05)
    assert metric.position == pos
    assert metric.value == 0.05

    exposure = Exposure(asset_class="equity", amount=1000.0)
    assert exposure.asset_class == "equity"
    assert exposure.amount == 1000.0


# === Bounded Context Architecture Tests ===


def test_domain_package_structure_exists() -> None:
    root = Path("src/tranalyset/domain")

    assert {path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith("__")} >= {
        "common",
        "market",
        "trading",
        "portfolio",
        "analytics",
        "risk",
        "strategy",
    }


def test_domain_models_are_frozen_slotted_dataclasses() -> None:
    from tranalyset.domain.analytics.models import IndicatorSnapshot, PerformanceMetric
    from tranalyset.domain.risk.models import RiskAssessment, RiskLimit
    from tranalyset.domain.strategy.models import StrategyDefinition, StrategyId

    model_types = (
        Symbol,
        Exchange,
        Currency,
        Quantity,
        Price,
        Money,
        Ratio,
        Instrument,
        PriceQuote,
        Tick,
        Candle,
        Order,
        Trade,
        Position,
        Portfolio,
        Signal,
        IndicatorSnapshot,
        PerformanceMetric,
        RiskLimit,
        RiskAssessment,
        StrategyId,
        StrategyDefinition,
    )

    for model_type in model_types:
        assert is_dataclass(model_type)
        assert model_type.__dataclass_params__.frozen is True
        assert hasattr(model_type, "__slots__")


def test_value_objects_are_immutable_and_hashable() -> None:
    symbol = Symbol("aapl")
    price = Price("100.25")
    quantity = Quantity("10")
    money = Money("1000", Currency("usd"))

    assert symbol.value == "AAPL"
    assert hash(symbol) == hash(Symbol("AAPL"))
    assert hash(price) == hash(Price("100.25"))
    assert hash(quantity) == hash(Quantity("10"))
    assert hash(money) == hash(Money("1000", Currency("USD")))
    with pytest.raises(FrozenInstanceError):
        symbol.value = "MSFT"  # type: ignore[misc]


def test_money_requires_same_currency_for_arithmetic() -> None:
    usd = Currency("USD")
    eur = Currency("EUR")

    assert Money("10", usd) + Money("5", usd) == Money("15", usd)
    with pytest.raises(ValueError, match="same currency"):
        Money("10", usd) + Money("5", eur)


def test_market_models_enforce_ohlc_and_order_book_invariants(aapl_instrument: Instrument) -> None:
    book = OrderBook(
        instrument=aapl_instrument,
        bids={Price(100): Quantity(10)},
        asks={Price(101): Quantity(20)},
    )

    assert book.best_bid == Price(100)
    assert book.best_ask == Price(101)
    assert book.spread() == Price(1)
    assert hash(book)
    with pytest.raises(ValueError, match="Best bid"):
        OrderBook(
            instrument=aapl_instrument,
            bids={Price(102): Quantity(10)},
            asks={Price(101): Quantity(20)},
        )


def test_market_liquidity_creation(aapl_instrument: Instrument) -> None:
    timestamp = datetime.now(UTC)
    liquidity = MarketLiquidity(
        instrument=aapl_instrument,
        timestamp=timestamp,
        bid_liquidity=Liquidity(1000),
        ask_liquidity=Liquidity(900),
    )

    assert liquidity.total == Liquidity(1900)
    assert liquidity.timestamp == timestamp
    with pytest.raises(ValueError, match="Open"):
        Candle(
            instrument=aapl_instrument,
            timestamp=timestamp,
            open=Price(105),
            high=Price(104),
            low=Price(100),
            close=Price(101),
            volume=Quantity(100),
        )


def test_timeframe_aggregates_ticks_without_mutating_inputs(aapl_instrument: Instrument) -> None:
    start = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    ticks = (
        Tick(aapl_instrument, start, Price(100), Quantity(10)),
        Tick(aapl_instrument, start.replace(second=20), Price(102), Quantity(5)),
        Tick(aapl_instrument, start.replace(minute=31), Price(101), Quantity(8)),
    )

    candles = Timeframe(60).aggregate(ticks)

    assert len(candles) == 2
    assert candles[0].open == Price(100)
    assert candles[0].high == Price(102)
    assert candles[0].close == Price(102)
    assert candles[0].volume == Quantity(15)
    assert ticks[0].price == Price(100)


def test_mapping_fields_are_immutable_and_hashable(aapl_instrument: Instrument) -> None:
    from tranalyset.domain.analytics.models import IndicatorSnapshot
    from tranalyset.domain.risk.models import RiskAssessment, RiskLimit
    from tranalyset.domain.strategy.models import StrategyDefinition, StrategyId

    timestamp = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    indicators = IndicatorSnapshot(
        instrument=aapl_instrument,
        timestamp=timestamp,
        timeframe="1m",
        indicators={"rsi": 55.0},
    )
    signal = Signal(
        signal_id="sig-1",
        instrument=aapl_instrument,
        side=OrderSide.BUY,
        strength=0.7,
        confidence=0.8,
        timestamp=timestamp,
        metadata={"source": "unit-test"},
    )
    strategy = StrategyDefinition(
        strategy_id=StrategyId("strategy-1"),
        name="Breakout",
        parameters={"lookback": 20},
    )
    risk = RiskAssessment(
        trade_id="trade-1",
        approved=True,
        observed_risk=Ratio.bounded(0.01),
        limits=(RiskLimit("max_trade_risk", Ratio.bounded(0.02)),),
        evidence={"portfolio_risk": 0.04},
    )

    with pytest.raises(TypeError):
        indicators.indicators["rsi"] = 60.0  # type: ignore[index]
    with pytest.raises(TypeError):
        signal.metadata["source"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        strategy.parameters["lookback"] = 50  # type: ignore[index]
    with pytest.raises(TypeError):
        risk.evidence["portfolio_risk"] = 0.1  # type: ignore[index]
    assert hash(indicators)
    assert hash(signal)
    assert hash(strategy)
    assert hash(risk)


def test_risk_assessment_rejects_inconsistent_approval() -> None:
    from tranalyset.domain.risk.models import RiskAssessment, RiskLimit

    with pytest.raises(ValueError, match="Approved risk assessment"):
        RiskAssessment(
            trade_id="trade-1",
            approved=True,
            observed_risk=Ratio.bounded(0.03),
            limits=(RiskLimit("max_trade_risk", Ratio.bounded(0.02)),),
        )


def test_domain_layer_has_no_infrastructure_imports() -> None:
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "pydantic",
    }
    forbidden_tranalyset_roots = {
        "tranalyset.api",
        "tranalyset.kernel",
        "tranalyset.plugin_sdk",
        "tranalyset.plugins",
        "tranalyset.contracts",
        "tranalyset.market_data",
        "tranalyset.strategy_sdk",
    }
    for path in Path("src/tranalyset/domain").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    assert module.split(".")[0] not in forbidden_roots
                    assert not any(module == root or module.startswith(f"{root}.") for root in forbidden_tranalyset_roots)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                assert module.split(".")[0] not in forbidden_roots
                assert not any(module == root or module.startswith(f"{root}.") for root in forbidden_tranalyset_roots)


def test_domain_models_do_not_contain_transport_serialization_methods() -> None:
    model_modules = (
        "tranalyset.domain.common.value_objects",
        "tranalyset.domain.market.models",
        "tranalyset.domain.trading.models",
        "tranalyset.domain.portfolio.models",
        "tranalyset.domain.analytics.models",
        "tranalyset.domain.risk.models",
        "tranalyset.domain.strategy.models",
    )
    forbidden = {"to_payload", "to_dict", "from_dict", "json", "model_dump"}

    for module_name in model_modules:
        module = __import__(module_name, fromlist=["*"])
        for _, model in inspect.getmembers(module, inspect.isclass):
            if not model.__module__.startswith("tranalyset.domain"):
                continue
            if not is_dataclass(model):
                continue
            assert forbidden.isdisjoint(vars(model))
