from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tranalyset.contracts.events import EventEnvelope
from tranalyset.plugins.paper_trading_engine.contracts import PAPER_TRADE_CLOSED
from tranalyset.plugins.quantitative_research_engine import (
    ResearchPeriod,
    ResearchSplit,
    StrategyEvaluationLaboratory,
    StrategyResearchInput,
)


BASE = datetime(2024, 1, 1, tzinfo=UTC)
SPLIT = ResearchSplit(
    training=ResearchPeriod("training", BASE, BASE + timedelta(days=10)),
    validation=ResearchPeriod("validation", BASE + timedelta(days=10), BASE + timedelta(days=20)),
    out_of_sample=ResearchPeriod("out_of_sample", BASE + timedelta(days=20), BASE + timedelta(days=30)),
)


def _closed(day: int, pnl: float, *, strategy_id: str = "alpha", occurred_at: datetime | None = None) -> EventEnvelope:
    timestamp = BASE + timedelta(days=day)
    return EventEnvelope.create(
        source="paper_trading_engine",
        event_type=PAPER_TRADE_CLOSED,
        timestamp=timestamp,
        payload={
            "trade_id": f"{strategy_id}-{day}-{pnl}",
            "strategy_id": strategy_id,
            "symbol": "TEST",
            "occurred_at": (occurred_at or timestamp).isoformat(),
            "quantity": 10.0,
            "average_price": 100.0,
            "exit_price": 100.0,
            "realized_pnl": pnl,
            "commissions": 0.0,
            "slippage": 0.0,
            "reason": "signal",
            "side": "long",
            "timeframe": "1m",
            "regime": "trend",
        },
    )


def _input(strategy_id: str, events: tuple[EventEnvelope, ...], parameter: int = 1) -> StrategyResearchInput:
    return StrategyResearchInput(
        strategy_id=strategy_id,
        events=events,
        parameters={"lookback": parameter},
        symbols=("TEST",),
        timeframes=("1m",),
        regimes=("trend",),
        sides=("long",),
    )


def test_laboratory_separates_periods_and_ignores_future_trade_information() -> None:
    laboratory = StrategyEvaluationLaboratory()
    events = (_closed(1, 10.0), _closed(11, -5.0), _closed(21, 1_000_000.0))

    report = laboratory.evaluate(inputs=(_input("alpha", events),), split=SPLIT, monte_carlo_trials=10)
    result = report.rankings[0]

    assert result.training["net_profit"] == 10.0
    assert result.validation["net_profit"] == -5.0
    assert result.out_of_sample["net_profit"] == 1_000_000.0
    assert result.training["trade_count"] == 1.0
    assert result.validation["trade_count"] == 1.0


def test_laboratory_rejects_trade_evidence_with_a_future_or_mismatched_timestamp() -> None:
    laboratory = StrategyEvaluationLaboratory()
    future = BASE + timedelta(days=21)
    event = _closed(1, 10.0, occurred_at=future)

    with pytest.raises(ValueError, match="timestamp"):
        laboratory.evaluate(inputs=(_input("alpha", (event,)),), split=SPLIT)


def test_laboratory_is_deterministic_and_does_not_modify_recorded_events() -> None:
    laboratory = StrategyEvaluationLaboratory()
    events = tuple(_closed(day, pnl) for day, pnl in ((1, 10.0), (2, -3.0), (11, 8.0), (12, -2.0), (21, 7.0), (22, -1.0)))
    before = tuple((event.timestamp, dict(event.payload)) for event in events)

    first = laboratory.evaluate(inputs=(_input("alpha", events),), split=SPLIT, monte_carlo_trials=40, seed=7)
    second = laboratory.evaluate(inputs=(_input("alpha", events),), split=SPLIT, monte_carlo_trials=40, seed=7)

    assert first == second
    assert tuple((event.timestamp, dict(event.payload)) for event in events) == before
    assert first.rankings[0].monte_carlo["p95_drawdown"] >= 0.0


def test_laboratory_ranks_by_observed_out_of_sample_performance() -> None:
    laboratory = StrategyEvaluationLaboratory()
    overfit = _input("overfit", (_closed(1, 10_000.0, strategy_id="overfit"), _closed(11, -100.0, strategy_id="overfit"), _closed(21, -100.0, strategy_id="overfit")))
    robust = _input("robust", (_closed(1, 100.0, strategy_id="robust"), _closed(11, 100.0, strategy_id="robust"), _closed(21, 100.0, strategy_id="robust")))

    report = laboratory.evaluate(inputs=(overfit, robust), split=SPLIT, monte_carlo_trials=10)

    assert [item.strategy_id for item in report.rankings] == ["robust", "overfit"]
    assert report.rankings[0].out_of_sample["total_return"] > report.rankings[1].out_of_sample["total_return"]


def test_laboratory_exposes_walk_forward_and_regime_results_without_cross_period_leakage() -> None:
    laboratory = StrategyEvaluationLaboratory()
    events = (_closed(1, 10.0), _closed(11, 20.0), _closed(21, 30.0))
    report = laboratory.evaluate(inputs=(_input("alpha", events),), split=SPLIT, walk_forward_splits=(SPLIT,))

    result = report.rankings[0]
    assert result.regime_metrics["trend"]["trade_count"] == 3.0
    assert report.walk_forward["alpha"][0]["net_profit"] == 30.0
    assert report.to_payload()["rankings"][0]["out_of_sample"]["net_profit"] == 30.0


def test_laboratory_metrics_match_hand_calculated_trade_ledger_values() -> None:
    events = (_closed(1, 100.0), _closed(11, -50.0), _closed(21, 200.0))
    report = StrategyEvaluationLaboratory().evaluate(
        inputs=(_input("alpha", events),),
        split=SPLIT,
        initial_capital=100_000.0,
        monte_carlo_trials=10,
    )

    result = report.rankings[0]
    assert result.training["total_return"] == pytest.approx(0.001)
    assert result.training["expectancy"] == 100.0
    assert result.training["win_rate"] == 1.0
    assert result.training["profit_factor"] == float("inf")
    assert result.training["sharpe_ratio"] is None
    assert result.validation["profit_factor"] == 0.0
    assert result.validation["win_rate"] == 0.0
    assert result.validation["drawdown"] == pytest.approx(0.0005)
    assert result.out_of_sample["net_profit"] == 200.0
    assert result.metadata["configuration"] == {"lookback": 1}
    assert result.metadata["execution_assumptions"] == {"requested_stress_bps": 5.0}
    assert result.metadata["strategy_version"] is None
    assert result.metadata["dataset_identity"]


def test_laboratory_does_not_rank_missing_or_incomplete_trade_evidence() -> None:
    input_without_trades = _input("alpha", ())
    report = StrategyEvaluationLaboratory().evaluate(inputs=(input_without_trades,), split=SPLIT)

    assert report.rankings == ()
    assert len(report.not_evaluated) == 1
    assert report.not_evaluated[0].status == "not_evaluated"
    assert report.not_evaluated[0].training["trade_count"] == 0.0
    assert report.not_evaluated[0].training["net_profit"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 0.0),
        ("average_price", float("nan")),
        ("commissions", -1.0),
        ("slippage", float("inf")),
    ],
)
def test_laboratory_rejects_invalid_closed_trade_ledger_fields(field: str, value: float) -> None:
    event = _closed(1, 10.0)
    payload = dict(event.payload)
    payload[field] = value
    invalid = EventEnvelope.create(
        source=event.source,
        event_type=event.event_type,
        timestamp=event.timestamp,
        payload=payload,
    )

    with pytest.raises(ValueError, match="Closed-trade"):
        StrategyEvaluationLaboratory().evaluate(inputs=(_input("alpha", (invalid,)),), split=SPLIT)


def test_parameter_configuration_does_not_change_recorded_trade_metrics() -> None:
    events = (_closed(1, 10.0), _closed(11, 20.0), _closed(21, 30.0))
    first = StrategyEvaluationLaboratory().evaluate(inputs=(_input("alpha", events, 1),), split=SPLIT)
    second = StrategyEvaluationLaboratory().evaluate(inputs=(_input("alpha", events, 999),), split=SPLIT)

    assert first.rankings[0].training == second.rankings[0].training
    assert first.rankings[0].validation == second.rankings[0].validation
    assert first.rankings[0].out_of_sample == second.rankings[0].out_of_sample
