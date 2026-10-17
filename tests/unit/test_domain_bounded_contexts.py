from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tranalyset.domain.analytics import (
    BacktestMetrics,
    IndicatorSeries,
    IndicatorValue,
    MarketMetric,
    MarketStructure,
    Momentum,
    MomentumDirection,
    Pattern,
    PatternConfidence,
    PerformanceMetric,
    PerformanceMetrics,
    StrategyStatistics,
    SupportResistance,
    TradeStatistics,
    Trend,
    TrendDirection,
    Volatility,
    VolatilityLevel,
    VolumeProfile,
)
from tranalyset.domain.common import AssetClass, OrderSide
from tranalyset.domain.common.value_objects import Currency, Liquidity, Money, Price, Quantity, Ratio, Symbol, Volume
from tranalyset.domain.events import DomainEvent, PatternDetected, PositionClosed, PositionOpened, RiskApproved, TradeClosed, TradeCreated, TradeHealthChanged
from tranalyset.domain.exceptions import (
    DomainValidationError,
    InvalidIndicator,
    InvalidMarketState,
    InvalidOrder,
    InvalidPortfolioState,
    InvalidPosition,
    InvalidPrice,
    InvalidQuantity,
    InvalidRiskConfiguration,
    InvalidTradeTransition,
)
from tranalyset.domain.factories import IndicatorFactory, MarketStateFactory, PortfolioFactory, RiskFactory, TradeFactory
from tranalyset.domain.market.models import Exchange, Instrument
from tranalyset.domain.portfolio import Holding, Portfolio
from tranalyset.domain.risk import (
    DrawdownLimit,
    ExposureLimit,
    LiquidityRisk,
    PositionSizing,
    RiskAssessment,
    RiskDecision,
    RiskDecisionOutcome,
    RiskEvidence,
    RiskLimit,
    RiskLimits,
    RiskProfile,
    RiskScore,
    StopLoss,
    StopLossType,
    TakeProfit,
    TakeProfitType,
    TrailingStop,
    TrailingStopType,
    VolatilityRisk,
)
from tranalyset.domain.services import (
    MarketStructureAnalyzer,
    MomentumAnalyzer,
    PatternConfidenceCalculator,
    PortfolioExposureCalculator,
    PositionSizeCalculator,
    RewardRiskCalculator,
    RiskEvaluator,
    SupportResistanceCalculator,
    TrendAnalyzer,
    VolatilityCalculator,
)
from tranalyset.domain.strategy import (
    Signal,
    Strategy,
    StrategyCandidate,
    StrategyConfiguration,
    StrategyDecision as StrategyDecisionModel,
    StrategyDecisionType,
    StrategyDefinition,
    StrategyEvaluation,
    StrategyId,
    StrategyMetadata,
    StrategyParameter,
    StrategyPerformance,
    StrategySignal,
    StrategyVersion,
)
from tranalyset.domain.trading import Trade


def _now() -> datetime:
    return datetime(2026, 7, 26, 9, 30, tzinfo=UTC)


def _instrument() -> Instrument:
    return Instrument(symbol=Symbol("AAPL"), exchange=Exchange("NASDAQ"), asset_class=AssetClass.EQUITY)


def _prices() -> list[Price]:
    return [Price(100), Price(101), Price(102), Price(104), Price(106)]


def test_domain_exception_hierarchy_is_consistent() -> None:
    assert issubclass(InvalidTradeTransition, DomainValidationError)
    assert issubclass(InvalidOrder, DomainValidationError)
    assert issubclass(InvalidRiskConfiguration, DomainValidationError)
    assert issubclass(InvalidMarketState, DomainValidationError)
    assert issubclass(InvalidPortfolioState, DomainValidationError)
    assert issubclass(InvalidPosition, DomainValidationError)
    assert issubclass(InvalidIndicator, DomainValidationError)
    assert issubclass(InvalidPrice, DomainValidationError)
    assert issubclass(InvalidQuantity, DomainValidationError)


def test_risk_models_validate_and_support_immutable_updates() -> None:
    limits = RiskLimits(
        drawdown_limit=DrawdownLimit(Ratio(0.2)),
        exposure_limits=(ExposureLimit(max_exposure=Money(5000, Currency("USD")), asset_class=AssetClass.EQUITY),),
        stop_loss=StopLoss(type=StopLossType.PRICE, price=Price(95)),
        take_profit=TakeProfit(type=TakeProfitType.PRICE, price=Price(120)),
        trailing_stop=TrailingStop(type=TrailingStopType.AMOUNT, amount=Price(3)),
        volatility_risk=VolatilityRisk(value=Ratio(0.3), lookback=20),
        liquidity_risk=LiquidityRisk(value=Ratio(0.2), liquidity=Liquidity(1000)),
    )
    profile = RiskProfile(profile_id="risk-1", name="Conservative", limits=limits)
    updated_profile = profile.with_limits(limits)
    sizing = PositionSizing(
        account_equity=Money(10_000, Currency("USD")),
        risk_per_trade=Ratio(0.02),
        entry_price=Price(100),
        stop_loss=StopLoss(type=StopLossType.PRICE, price=Price(95)),
        quantity=Quantity(10),
        risk_amount=Money(200, Currency("USD")),
        percentage_of_equity=Ratio(0.1),
    )
    score = RiskScore(42, label="moderate")
    decision = RiskDecision(decision_id="rd-1", outcome=RiskDecisionOutcome.APPROVED, score=score, reason="Within bounds")
    assessment = RiskAssessment(
        trade_id="trade-1",
        approved=True,
        observed_risk=Ratio(0.01),
        limits=(RiskLimit("max_trade_risk", Ratio(0.2)),),
        risk_profile=profile,
        risk_score=score,
        decision=decision,
        evidence={"portfolio_risk": 0.04},
    )

    assert profile == updated_profile
    assert hash(profile)
    assert sizing.quantity == Quantity(10)
    assert assessment.decision == decision
    assert assessment.risk_score == score
    assert assessment.evidence["portfolio_risk"] == 0.04

    with pytest.raises(ValueError, match="violate supplied risk limits"):
        RiskAssessment(
            trade_id="trade-2",
            approved=True,
            observed_risk=Ratio(0.5),
            limits=(RiskLimit("max_trade_risk", Ratio(0.2)),),
        )


def test_indicator_series_and_market_metrics_are_immutable() -> None:
    instrument = _instrument()
    first = IndicatorValue(name="rsi", value=55, timestamp=_now(), instrument=instrument, timeframe="1m")
    second = IndicatorValue(name="rsi", value=60, timestamp=_now().replace(minute=31), instrument=instrument, timeframe="1m")
    series = IndicatorSeries(name="rsi", values=(first, second), instrument=instrument)
    metric = MarketMetric(instrument=instrument, timestamp=_now(), value=123.4)
    snapshot = PerformanceMetric(name="win_rate", value=0.61)

    assert series.latest == second
    assert metric.value == Decimal("123.400000")
    assert snapshot.value == Decimal("0.610000")
    assert hash(series)
    with pytest.raises(ValueError, match="strictly increasing"):
        series.append(first)


def test_analytics_summary_models_validate_invariants() -> None:
    instrument = _instrument()
    trend = Trend(direction=TrendDirection.UP, strength=Ratio(0.8), slope=Decimal("0.2"))
    momentum = Momentum(value=Ratio(0.4), direction=MomentumDirection.ACCELERATING)
    volatility = Volatility(value=Ratio(0.25), level=VolatilityLevel.HIGH, lookback=20)
    sr = SupportResistance(support=Price(100), resistance=Price(120))
    structure = MarketStructure(trend=trend, momentum=momentum, volatility=volatility, support_resistance=sr, regime="breakout")
    profile = VolumeProfile(
        symbol=Symbol("AAPL"),
        timeframe="1h",
        price_levels=((Price(100), Volume(50)), (Price(110), Volume(80))),
        point_of_control=Price(110),
        value_area_high=Price(115),
        value_area_low=Price(95),
        timestamp=_now(),
    )
    trade_stats = TradeStatistics(
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        win_rate=Ratio(0.6),
        average_profit=Money(100, Currency("USD")),
        average_loss=Money(50, Currency("USD")),
        profit_factor=Ratio(1.5),
        expectancy=Money(20, Currency("USD")),
        max_drawdown=Money(80, Currency("USD")),
        sharpe_ratio=1.2,
        sortino_ratio=1.5,
        timestamp=_now(),
    )
    strategy_stats = StrategyStatistics(
        strategy_id="strategy-1",
        total_return=Ratio(0.25),
        annualized_return=Ratio(0.4),
        max_drawdown=Ratio(0.1),
        sharpe_ratio=1.0,
        sortino_ratio=1.2,
        win_rate=Ratio(0.6),
        profit_factor=Ratio(1.4),
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        average_trade=Money(20, Currency("USD")),
        expectancy=Money(12, Currency("USD")),
        volatility=Ratio(0.2),
        timestamp=_now(),
    )
    backtest = BacktestMetrics(
        strategy_id="strategy-1",
        start_date=_now(),
        end_date=_now().replace(hour=10),
        initial_capital=Money(10_000, Currency("USD")),
        final_capital=Money(11_200, Currency("USD")),
        total_return=Ratio(0.12),
        annualized_return=Ratio(0.18),
        max_drawdown=Ratio(0.08),
        sharpe_ratio=1.3,
        sortino_ratio=1.7,
        win_rate=Ratio(0.57),
        profit_factor=Ratio(1.6),
        total_trades=20,
        winning_trades=11,
        losing_trades=9,
        expectancy=Money(60, Currency("USD")),
    )
    performance = PerformanceMetrics(
        period_start=_now(),
        period_end=_now().replace(hour=10),
        starting_equity=Money(10_000, Currency("USD")),
        ending_equity=Money(11_250, Currency("USD")),
        absolute_return=Money(1_250, Currency("USD")),
        relative_return=Ratio(0.125),
        annualized_return=Ratio(0.2),
        max_drawdown=Money(300, Currency("USD")),
        max_drawdown_percent=Ratio(0.03),
        sharpe_ratio=1.4,
        sortino_ratio=1.8,
        profit_factor=Ratio(1.7),
        win_rate=Ratio(0.58),
        total_trades=20,
        winning_trades=12,
        losing_trades=8,
        expectancy=Money(62, Currency("USD")),
        volatility=Ratio(0.19),
        timestamp=_now(),
    )

    assert structure.regime == "breakout"
    assert profile.point_of_control == Price(110)
    assert trade_stats.win_rate == Ratio(0.6)
    assert strategy_stats.total_trades == 10
    assert backtest.final_capital == Money(11_200, Currency("USD"))
    assert performance.absolute_return == Money(1_250, Currency("USD"))


def test_strategy_models_validate_and_hash() -> None:
    instrument = _instrument()
    strategy_id = StrategyId("strategy-1")
    parameter = StrategyParameter(name="lookback", value=20)
    config = StrategyConfiguration(strategy_id=strategy_id, parameters=(parameter,))
    metadata = StrategyMetadata(strategy_id=strategy_id, name="Breakout", author="Codex", instruments=(instrument,))
    version = StrategyVersion(strategy_id=strategy_id, version_number="1.0.0", configuration=config)
    signal = StrategySignal(
        signal_id="signal-1",
        instrument=instrument,
        side=OrderSide.BUY,
        strength=Ratio(0.7),
        confidence=Ratio(0.8),
        timestamp=_now(),
        metadata={"source": "unit-test"},
    )
    candidate = StrategyCandidate(
        candidate_id="candidate-1",
        strategy_id=strategy_id,
        name="Breakout candidate",
        description="simple candidate",
        generation_method="grid",
        fitness_score=Ratio(0.75),
        parameters=(parameter,),
        metadata={"score": 10},
    )
    decision = StrategyDecisionModel(
        decision_id="decision-1",
        strategy_id=strategy_id,
        outcome=StrategyDecisionType.ACCEPT,
        reason="meets criteria",
    )
    evaluation = StrategyEvaluation(
        evaluation_id="evaluation-1",
        strategy_id=strategy_id,
        decision=StrategyDecisionType.ACCEPT,
        score=Ratio(0.9),
        feedback="good",
        metrics=None,
    )
    performance = StrategyPerformance(
        strategy_id=strategy_id,
        period_start=_now(),
        period_end=_now().replace(hour=10),
        total_return=Ratio(0.2),
        annualized_return=Ratio(0.3),
        max_drawdown=Ratio(0.1),
        sharpe_ratio=1.1,
        sortino_ratio=1.4,
        win_rate=Ratio(0.6),
        profit_factor=Ratio(1.5),
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        average_trade=Money(20, Currency("USD")),
        expectancy=Money(15, Currency("USD")),
        volatility=Ratio(0.2),
    )
    strategy = Strategy(strategy_id=strategy_id, metadata=metadata, configuration=config, current_version=version)
    legacy = StrategyDefinition(strategy_id=strategy_id, name="Breakout", parameters={"lookback": 20})

    assert signal.strategy_id is None
    assert hash(config)
    assert candidate.metadata["score"] == 10
    assert decision.outcome == StrategyDecisionType.ACCEPT
    assert evaluation.score == Ratio(0.9)
    assert performance.total_trades == 10
    assert strategy.current_version == version
    assert legacy.parameters["lookback"] == 20


def test_service_calculators_return_domain_results() -> None:
    instrument = _instrument()
    risk_result = RewardRiskCalculator.calculate(
        entry_price=Price(100),
        stop_loss=StopLoss(type=StopLossType.PRICE, price=Price(95)),
        take_profit=TakeProfit(type=TakeProfitType.PRICE, price=Price(115)),
    )
    size_result = PositionSizeCalculator.fixed_fractional(
        account_equity=Money(10_000, Currency("USD")),
        risk_per_trade=Ratio(0.02),
        entry_price=Price(100),
        stop_loss=StopLoss(type=StopLossType.PRICE, price=Price(95)),
    )
    trend_result = TrendAnalyzer.analyze(_prices(), lookback=5)
    volatility_result = VolatilityCalculator.calculate_historical_volatility(_prices(), trading_periods_per_year=252)
    support_resistance_result = SupportResistanceCalculator.calculate(_prices())
    momentum_result = MomentumAnalyzer.analyze(_prices(), lookback=5)
    market_structure_result = MarketStructureAnalyzer.analyze(_prices(), lookback=5)

    assert risk_result.ratio == Ratio(3)
    assert size_result.quantity > 0
    assert trend_result.direction == TrendDirection.UP
    assert volatility_result.value.value >= 0
    assert support_resistance_result.support == Price(100)
    assert momentum_result.direction == MomentumDirection.ACCELERATING
    assert market_structure_result.structure_type in {"trending_up", "breakout"}


def test_factories_and_internal_events_are_immutable() -> None:
    timestamp = _now()
    value = IndicatorFactory.create_value("rsi", 55, timestamp, instrument=_instrument(), timeframe="1m")
    series = IndicatorFactory.create_series("rsi", (value,), instrument=_instrument())
    portfolio = PortfolioFactory.create_portfolio("p-1", "acc-1", 10_000)
    risk_profile = RiskFactory.create_profile("risk-1", "Conservative")
    trade = TradeFactory.create_trade("trade-1", "order-1", _instrument(), OrderSide.BUY, Quantity(10), Price(100), timestamp)
    event = TradeCreated(event_id="evt-1", source="domain", payload={"trade_id": "trade-1"}, occurred_at=timestamp)
    closed = TradeClosed(event_id="evt-2", source="domain", payload={"trade_id": "trade-1"}, occurred_at=timestamp)
    opened = PositionOpened(event_id="evt-3", source="domain", payload={"symbol": "AAPL"}, occurred_at=timestamp)
    risk = RiskApproved(event_id="evt-4", source="domain", payload={"trade_id": "trade-1"}, occurred_at=timestamp)
    pattern = PatternDetected(event_id="evt-5", source="domain", payload={"pattern": "breakout"}, occurred_at=timestamp)
    health = TradeHealthChanged(event_id="evt-6", source="domain", payload={"score": 85}, occurred_at=timestamp)
    generic = DomainEvent(event_id="evt-7", source="domain", event_type="Custom", payload={"key": "value"}, occurred_at=timestamp)

    assert series.latest == value
    assert portfolio.balance == Decimal("10000.000000")
    assert risk_profile.name == "Conservative"
    assert trade.price == Price(100)
    assert event.event_type == "TradeCreated"
    assert closed.event_type == "TradeClosed"
    assert opened.event_type == "PositionOpened"
    assert risk.event_type == "RiskApproved"
    assert pattern.event_type == "PatternDetected"
    assert health.event_type == "TradeHealthChanged"
    assert generic.payload["key"] == "value"
    with pytest.raises(TypeError):
        generic.payload["key"] = "changed"  # type: ignore[index]
