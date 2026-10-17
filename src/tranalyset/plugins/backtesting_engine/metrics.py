from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from tranalyset.contracts.events import EventEnvelope, ImmutableValue
from tranalyset.plugins.paper_trading_engine.contracts import (
    PAPER_PORTFOLIO_UPDATED,
    PAPER_EXECUTION_RECORDED,
    PAPER_TRADE_CLOSED,
)


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    trade_id: str
    symbol: str
    closed_at: datetime
    realized_pnl: float
    reward_risk: float | None = None

    def to_payload(self) -> dict[str, float | str]:
        payload: dict[str, float | str] = {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "closed_at": self.closed_at.isoformat(),
            "realized_pnl": self.realized_pnl,
        }
        if self.reward_risk is not None:
            payload["reward_risk"] = self.reward_risk
        return payload


@dataclass(frozen=True, slots=True)
class EquityPoint:
    occurred_at: datetime
    equity: float
    cash: float
    realized_pnl: float
    unrealized_pnl: float

    def to_payload(self) -> dict[str, float | str]:
        return {
            "occurred_at": self.occurred_at.isoformat(),
            "equity": self.equity,
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
        }


@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: str
    strategy_id: str
    mode: str
    started_at: datetime
    completed_at: datetime
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    win_rate: float | None
    profit_factor: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    drawdown: float | None
    average_rr: float | None
    maximum_consecutive_loss: int
    maximum_consecutive_win: int
    daily_returns: Mapping[str, float | None]
    monthly_returns: Mapping[str, float | None]
    total_return: float | None = 0.0
    cagr: float | None = 0.0
    loss_rate: float | None = 0.0
    expectancy: float | None = 0.0
    average_win: float | None = 0.0
    average_loss: float | None = 0.0
    average_drawdown: float | None = 0.0
    recovery_factor: float | None = 0.0
    calmar_ratio: float | None = 0.0
    average_holding_time_seconds: float | None = 0.0
    trade_frequency: float | None = 0.0
    exposure: float | None = 0.0
    turnover: float | None = 0.0
    regime_performance: Mapping[str, float] = field(default_factory=dict)
    long_short_performance: Mapping[str, float] = field(default_factory=dict)
    strategy_performance: Mapping[str, float] = field(default_factory=dict)
    evidence: tuple[EventEnvelope, ...] = ()
    status: str = "not_evaluated"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def metrics_payload(self) -> dict[str, float | int | str | None]:
        return {
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "drawdown": self.drawdown,
            "average_rr": self.average_rr,
            "maximum_consecutive_loss": self.maximum_consecutive_loss,
            "maximum_consecutive_win": self.maximum_consecutive_win,
            "trade_count": len(self.trades),
            "total_return": self.total_return,
            "cagr": self.cagr,
            "loss_rate": self.loss_rate,
            "expectancy": self.expectancy,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "average_drawdown": self.average_drawdown,
            "recovery_factor": self.recovery_factor,
            "calmar_ratio": self.calmar_ratio,
            "average_holding_time_seconds": self.average_holding_time_seconds,
            "trade_frequency": self.trade_frequency,
            "exposure": self.exposure,
            "turnover": self.turnover,
            "status": self.status,
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "metrics": self.metrics_payload(),
            "trades": tuple(trade.to_payload() for trade in self.trades),
            "equity_curve": tuple(point.to_payload() for point in self.equity_curve),
            "daily_returns": dict(self.daily_returns),
            "monthly_returns": dict(self.monthly_returns),
            "evidence": tuple(_evidence_payload(event) for event in self.evidence),
            "regime_performance": dict(self.regime_performance),
            "long_short_performance": dict(self.long_short_performance),
            "strategy_performance": dict(self.strategy_performance),
            "metadata": dict(self.metadata),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class BacktestComparison:
    results: tuple[BacktestResult, ...]
    ranking: tuple[Mapping[str, float | int | str], ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "results": tuple(result.to_payload() for result in self.results),
            "ranking": tuple(dict(item) for item in self.ranking),
        }


@dataclass(slots=True)
class BacktestRecorder:
    initial_capital: float
    _trades: list[BacktestTrade] = field(default_factory=list)
    _equity_curve: list[EquityPoint] = field(default_factory=list)
    _evidence: list[EventEnvelope] = field(default_factory=list)
    _opened_at: dict[str, datetime] = field(default_factory=dict)
    _fills: dict[str, list[dict[str, float | str]]] = field(default_factory=dict)
    _closed_trade_ids: set[str] = field(default_factory=set)
    _last_recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive.")

    def reset(self) -> None:
        self._trades.clear()
        self._equity_curve.clear()
        self._evidence.clear()
        self._opened_at.clear()
        self._fills.clear()
        self._closed_trade_ids.clear()
        self._last_recorded_at = None

    @property
    def trades(self) -> tuple[BacktestTrade, ...]:
        return tuple(self._trades)

    @property
    def equity_curve(self) -> tuple[EquityPoint, ...]:
        return tuple(self._equity_curve)

    def record(self, event: EventEnvelope) -> None:
        if event.timestamp.tzinfo is None:
            raise ValueError("Backtest event timestamps must be timezone-aware.")
        self._evidence.append(event)
        is_evidence = (
            str(event.event_type) == "PaperTradeUpdated" and event.payload.get("status") == "opened"
        ) or event.event_type in {PAPER_TRADE_CLOSED, PAPER_PORTFOLIO_UPDATED, PAPER_EXECUTION_RECORDED}
        if is_evidence:
            if event.timestamp.tzinfo is None:
                raise ValueError("Backtest evidence timestamps must be timezone-aware.")
            if self._last_recorded_at is not None and event.timestamp < self._last_recorded_at:
                raise ValueError("Backtest ledger timestamps must be chronological.")
            self._last_recorded_at = event.timestamp
        if str(event.event_type) == "PaperTradeUpdated" and event.payload.get("status") == "opened":
            trade_id = _text(event.payload.get("trade_id"), default="")
            if not trade_id:
                raise ValueError("Opened trade evidence requires a trade_id.")
            self._opened_at.setdefault(trade_id, _payload_timestamp(event, "occurred_at"))
        if event.event_type == PAPER_TRADE_CLOSED:
            self._record_trade(event)
        elif event.event_type == PAPER_PORTFOLIO_UPDATED:
            self._record_equity(event)
        elif event.event_type == PAPER_EXECUTION_RECORDED:
            self._record_fill(event)

    def build_result(
        self,
        *,
        run_id: str,
        strategy_id: str,
        mode: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> BacktestResult:
        if started_at.tzinfo is None or completed_at.tzinfo is None or started_at > completed_at:
            raise ValueError("Backtest evaluation timestamps must be timezone-aware and ordered.")
        if any(event.timestamp < started_at or event.timestamp > completed_at for event in self._evidence):
            raise ValueError("Backtest evidence timestamps must fall within the evaluation period.")
        daily_returns = _period_returns(self._equity_curve, "daily", self.initial_capital)
        monthly_returns = _period_returns(self._equity_curve, "monthly", self.initial_capital)
        complete = bool(self._trades and self._equity_curve)
        net_profit = self._equity_curve[-1].equity - self.initial_capital if self._equity_curve else None
        total_return = net_profit / self.initial_capital if net_profit is not None else None
        years = max((completed_at - started_at).total_seconds() / (365.25 * 86400), 0.0)
        cagr = (
            ((1.0 + total_return) ** (1.0 / years) - 1.0)
            if years >= 1.0 / 365.25 and total_return is not None and total_return > -1.0
            else None
        )
        drawdowns = _drawdowns(self._equity_curve, self.initial_capital)
        absolute_drawdowns = _absolute_drawdowns(self._equity_curve, self.initial_capital)
        holding_seconds = [
            (trade.closed_at - self._opened_at[trade.trade_id]).total_seconds()
            for trade in self._trades
            if trade.trade_id in self._opened_at
        ]
        regime_performance: dict[str, float] = {}
        for trade in self._trades:
            regime = _regime_at(self._evidence, trade.symbol, trade.closed_at)
            regime_performance[regime] = regime_performance.get(regime, 0.0) + trade.realized_pnl
        metadata = _backtest_metadata(
            self._evidence,
            started_at=started_at,
            completed_at=completed_at,
            initial_capital=self.initial_capital,
        )
        return BacktestResult(
            run_id=run_id,
            strategy_id=strategy_id,
            mode=mode,
            started_at=started_at,
            completed_at=completed_at,
            trades=tuple(self._trades),
            equity_curve=tuple(self._equity_curve),
            win_rate=_win_rate(self._trades),
            profit_factor=_profit_factor(self._trades),
            sharpe_ratio=_sharpe_ratio(tuple(daily_returns.values())),
            sortino_ratio=_sortino_ratio(tuple(daily_returns.values())),
            drawdown=_max_drawdown(self._equity_curve, self.initial_capital),
            average_rr=_average_reward_risk(self._trades),
            maximum_consecutive_loss=_max_streak(self._trades, winning=False),
            maximum_consecutive_win=_max_streak(self._trades, winning=True),
            daily_returns=daily_returns,
            monthly_returns=monthly_returns,
            total_return=total_return,
            cagr=cagr,
            loss_rate=_loss_rate(self._trades),
            expectancy=_expectancy(self._trades),
            average_win=_average(self._trades, positive=True),
            average_loss=_average(self._trades, positive=False),
            average_drawdown=(sum(drawdowns) / len(drawdowns)) if drawdowns else None,
            recovery_factor=(net_profit / max(absolute_drawdowns)) if net_profit is not None and absolute_drawdowns and max(absolute_drawdowns) > 0 else None,
            calmar_ratio=(cagr / max(drawdowns)) if cagr is not None and drawdowns and max(drawdowns) > 0 else None,
            average_holding_time_seconds=(sum(holding_seconds) / len(holding_seconds)) if holding_seconds else None,
            trade_frequency=(len(self._trades) / years) if years > 0 else 0.0,
            exposure=None,
            turnover=None,
            regime_performance=regime_performance,
            long_short_performance={},
            strategy_performance={},
            evidence=tuple(self._evidence),
            status="evaluated" if complete else "not_evaluated",
            metadata=metadata,
        )

    def _record_trade(self, event: EventEnvelope) -> None:
        trade_id = _text(event.payload.get("trade_id"), default="")
        symbol = _text(event.payload.get("symbol"), default="")
        realized_pnl = _required_number(event.payload.get("realized_pnl"), "realized_pnl")
        reward_risk = _reward_risk(event.payload)
        if not trade_id or not symbol:
            raise ValueError("Closed-trade ledger entries require trade_id and symbol.")
        if trade_id in self._closed_trade_ids:
            raise ValueError(f"Duplicate closed-trade ledger entry: {trade_id}.")
        self._closed_trade_ids.add(trade_id)
        _positive_number(event.payload.get("quantity"), "quantity")
        _positive_number(event.payload.get("average_price"), "average_price")
        _positive_number(event.payload.get("exit_price"), "exit_price")
        _nonnegative_number(event.payload.get("commissions"), "commissions")
        _nonnegative_number(event.payload.get("slippage"), "slippage")
        closed_at = _payload_timestamp(event, "occurred_at")
        if closed_at != event.timestamp:
            raise ValueError("Closed-trade payload timestamp must match its event timestamp.")
        self._validate_fills(trade_id, event.payload)
        self._trades.append(
            BacktestTrade(
                trade_id=trade_id,
                symbol=symbol,
                closed_at=closed_at,
                realized_pnl=realized_pnl,
                reward_risk=reward_risk,
            )
        )

    def _record_fill(self, event: EventEnvelope) -> None:
        action = _text(event.payload.get("action"), default="")
        if action not in {"fill", "exit_fill"}:
            return
        trade_id = _text(event.payload.get("trade_id"), default="")
        symbol = _text(event.payload.get("symbol"), default="")
        evidence = event.payload.get("evidence")
        if not trade_id or not symbol or not isinstance(evidence, Mapping):
            raise ValueError("Fill evidence requires trade_id, symbol, and an evidence mapping.")
        if event.source != "paper_trading_engine":
            raise ValueError("Fill evidence must originate from paper_trading_engine.")
        occurred_at = _payload_timestamp(event, "occurred_at")
        if occurred_at != event.timestamp:
            raise ValueError("Fill payload timestamp must match its event timestamp.")
        fill = {
            "action": action,
            "quantity": _positive_number(evidence.get("quantity"), "fill quantity"),
            "price": _positive_number(evidence.get("price"), "fill price"),
            "commission": _nonnegative_number(evidence.get("commission"), "fill commission"),
            "slippage": _nonnegative_number(evidence.get("slippage"), "fill slippage"),
        }
        if action == "exit_fill":
            fill["realized_pnl"] = _required_number(evidence.get("realized_pnl"), "fill realized_pnl")
        self._fills.setdefault(trade_id, []).append(fill)

    def _validate_fills(self, trade_id: str, trade: Mapping[str, ImmutableValue]) -> None:
        fills = self._fills.pop(trade_id, [])
        if not fills:
            return
        entry_fills = [fill for fill in fills if fill["action"] == "fill"]
        exit_fills = [fill for fill in fills if fill["action"] == "exit_fill"]
        if not entry_fills or not exit_fills:
            raise ValueError(f"Trade {trade_id} has an incomplete fill ledger.")
        entry_quantity = sum(float(fill["quantity"]) for fill in entry_fills)
        exit_quantity = sum(float(fill["quantity"]) for fill in exit_fills)
        if not math.isclose(entry_quantity, exit_quantity, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"Trade {trade_id} entry and exit fill quantities do not reconcile.")
        fees = sum(float(fill["commission"]) for fill in fills)
        slippage = sum(float(fill["slippage"]) for fill in fills)
        if not math.isclose(fees, float(trade["commissions"]), rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError(f"Trade {trade_id} commissions do not reconcile with fills.")
        if not math.isclose(slippage, float(trade["slippage"]), rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError(f"Trade {trade_id} slippage does not reconcile with fills.")
        net_pnl = sum(float(fill["realized_pnl"]) for fill in exit_fills) - sum(
            float(fill["commission"]) for fill in entry_fills
        )
        if not math.isclose(net_pnl, float(trade["realized_pnl"]), rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError(f"Trade {trade_id} realized P&L does not reconcile with fills.")
        average_entry = sum(float(fill["quantity"]) * float(fill["price"]) for fill in entry_fills) / entry_quantity
        if not math.isclose(average_entry, float(trade["average_price"]), rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError(f"Trade {trade_id} average entry price does not reconcile with fills.")

    def _record_equity(self, event: EventEnvelope) -> None:
        equity = _required_number(event.payload.get("equity"), "equity")
        cash = _required_number(event.payload.get("cash"), "cash")
        if equity < 0:
            raise ValueError("Equity values must not be negative.")
        occurred_at = _payload_timestamp(event, "occurred_at")
        if occurred_at != event.timestamp:
            raise ValueError("Equity payload timestamp must match its event timestamp.")
        self._equity_curve.append(
            EquityPoint(
                occurred_at=occurred_at,
                equity=equity,
                cash=cash,
                realized_pnl=_required_number(event.payload.get("realized_pnl"), "realized_pnl"),
                unrealized_pnl=_required_number(event.payload.get("unrealized_pnl"), "unrealized_pnl"),
            )
        )


def compare_strategies(results: Sequence[BacktestResult]) -> BacktestComparison:
    ranking = sorted(
        (
            {
                "strategy_id": result.strategy_id,
                "run_id": result.run_id,
                "profit_factor": result.profit_factor,
                "sharpe_ratio": result.sharpe_ratio,
                "drawdown": result.drawdown,
                "net_profit": _net_profit(result),
                "rank": 0,
            }
            for result in results
            if (
                result.status == "evaluated"
                and result.trades
                and result.equity_curve
                and "dataset_identity" in result.metadata
                and "initial_capital" in result.metadata
            )
        ),
        key=lambda item: (
            _ranking_number(item["profit_factor"], missing=-math.inf),
            _ranking_number(item["sharpe_ratio"], missing=-math.inf),
            _ranking_number(item["net_profit"], missing=-math.inf),
            -_ranking_number(item["drawdown"], missing=math.inf),
        ),
        reverse=True,
    )
    ranked = tuple({**item, "rank": index + 1} for index, item in enumerate(ranking))
    return BacktestComparison(results=tuple(results), ranking=ranked)


def export_result(result: BacktestResult, export_format: Literal["json", "csv"]) -> str:
    if export_format == "json":
        return json.dumps(_json_ready(result.to_payload()), allow_nan=False, sort_keys=True)
    if export_format == "csv":
        return _export_csv(result)
    msg = f"Unsupported export format: {export_format}"
    raise ValueError(msg)


def _export_csv(result: BacktestResult) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("section", "key", "value"))
    for key, value in result.metrics_payload().items():
        writer.writerow(("metrics", key, value))
    for point in result.equity_curve:
        writer.writerow(("equity_curve", point.occurred_at.isoformat(), point.equity))
    for trade in result.trades:
        writer.writerow(("trade", trade.trade_id, trade.realized_pnl))
    return output.getvalue()


def _json_ready(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return tuple(_json_ready(item) for item in value)
    return value


def _period_returns(
    equity_curve: Sequence[EquityPoint],
    period: Literal["daily", "monthly"],
    initial_capital: float,
) -> dict[str, float | None]:
    closing_equity: dict[str, float] = {}
    for point in equity_curve:
        if period == "daily":
            key = point.occurred_at.date().isoformat()
        else:
            key = point.occurred_at.strftime("%Y-%m")
        closing_equity[key] = point.equity

    returns: dict[str, float | None] = {}
    previous = initial_capital
    for key in sorted(closing_equity):
        current = closing_equity[key]
        returns[key] = None if previous <= 0 else (current - previous) / previous
        previous = current
    return returns


def _win_rate(trades: Sequence[BacktestTrade]) -> float | None:
    if not trades:
        return None
    wins = sum(1 for trade in trades if trade.realized_pnl > 0)
    return wins / len(trades)


def _profit_factor(trades: Sequence[BacktestTrade]) -> float | None:
    gross_profit = sum(trade.realized_pnl for trade in trades if trade.realized_pnl > 0)
    gross_loss = abs(sum(trade.realized_pnl for trade in trades if trade.realized_pnl < 0))
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else None
    return gross_profit / gross_loss


def _sharpe_ratio(returns: Sequence[float | None]) -> float | None:
    if len(returns) < 2 or any(value is None or not math.isfinite(value) for value in returns):
        return None
    usable = [float(value) for value in returns if value is not None]
    mean_return = sum(usable) / len(usable)
    standard_deviation = _sample_standard_deviation(usable)
    if standard_deviation == 0:
        return None
    return (mean_return / standard_deviation) * math.sqrt(252)


def _sortino_ratio(returns: Sequence[float | None]) -> float | None:
    if len(returns) < 2 or any(value is None or not math.isfinite(value) for value in returns):
        return None
    usable = [float(value) for value in returns if value is not None]
    downside_returns = tuple(value for value in usable if value < 0)
    if not downside_returns:
        return None
    mean_return = sum(usable) / len(usable)
    downside_deviation = math.sqrt(sum(value * value for value in downside_returns) / len(downside_returns))
    if downside_deviation == 0:
        return None
    return (mean_return / downside_deviation) * math.sqrt(252)


def _sample_standard_deviation(values: Sequence[float]) -> float:
    mean_value = sum(values) / len(values)
    variance = sum((value - mean_value) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _max_drawdown(equity_curve: Sequence[EquityPoint], initial_capital: float) -> float | None:
    return max(_drawdowns(equity_curve, initial_capital), default=0.0) if equity_curve else None


def _drawdowns(equity_curve: Sequence[EquityPoint], initial_capital: float) -> list[float]:
    peak = initial_capital
    drawdowns: list[float] = []
    for point in equity_curve:
        peak = max(peak, point.equity)
        if peak > 0:
            drawdowns.append((peak - point.equity) / peak)
    return drawdowns


def _absolute_drawdowns(equity_curve: Sequence[EquityPoint], initial_capital: float) -> list[float]:
    peak = initial_capital
    drawdowns: list[float] = []
    for point in equity_curve:
        peak = max(peak, point.equity)
        drawdowns.append(max(0.0, peak - point.equity))
    return drawdowns


def _loss_rate(trades: Sequence[BacktestTrade]) -> float | None:
    return None if not trades else sum(trade.realized_pnl < 0 for trade in trades) / len(trades)


def _expectancy(trades: Sequence[BacktestTrade]) -> float | None:
    return sum(trade.realized_pnl for trade in trades) / len(trades) if trades else None


def _average(trades: Sequence[BacktestTrade], *, positive: bool) -> float | None:
    values = [trade.realized_pnl for trade in trades if (trade.realized_pnl > 0) == positive and trade.realized_pnl != 0]
    return sum(values) / len(values) if values else None


def _evidence_payload(event: EventEnvelope) -> dict[str, Any]:
    return {
        "timestamp": event.timestamp.isoformat(),
        "source": event.source,
        "event_type": str(event.event_type),
        "payload": dict(event.payload),
        "metadata": dict(event.metadata),
    }


def _backtest_metadata(
    events: Sequence[EventEnvelope],
    *,
    started_at: datetime,
    completed_at: datetime,
    initial_capital: float,
) -> Mapping[str, Any]:
    dataset_events = tuple(event for event in events if event.source != "backtesting_engine")
    serialized = json.dumps(
        [_evidence_payload(event) for event in dataset_events],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    trades = tuple(event for event in dataset_events if event.event_type == PAPER_TRADE_CLOSED)
    return {
        "dataset_identity": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "date_range": {
            "start": min((event.timestamp for event in dataset_events), default=started_at).isoformat(),
            "end": max((event.timestamp for event in dataset_events), default=completed_at).isoformat(),
        },
        "strategy_version": None,
        "configuration": {"initial_capital": initial_capital},
        "execution_assumptions": {
            "fees_included_in_realized_pnl": True,
            "observed_fees": sum(float(event.payload["commissions"]) for event in trades),
            "observed_slippage": sum(float(event.payload["slippage"]) for event in trades),
        },
        "evaluation_period": {"start": started_at.isoformat(), "end": completed_at.isoformat()},
        "initial_capital": initial_capital,
    }


def _regime_at(events: Sequence[EventEnvelope], symbol: str, timestamp: datetime) -> str:
    matching = (
        event for event in events
        if str(event.event_type) == "MarketRegimeChanged"
        and _text(event.payload.get("symbol"), default="") == symbol
        and event.timestamp <= timestamp
    )
    latest = max(matching, key=lambda event: event.timestamp, default=None)
    return _text(latest.payload.get("regime"), default="Unknown") if latest is not None else "Unknown"


def _average_reward_risk(trades: Sequence[BacktestTrade]) -> float | None:
    ratios = tuple(trade.reward_risk for trade in trades if trade.reward_risk is not None)
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def _max_streak(trades: Sequence[BacktestTrade], *, winning: bool) -> int:
    target = 1 if winning else -1
    longest = 0
    current = 0
    for trade in trades:
        direction = 1 if trade.realized_pnl > 0 else -1 if trade.realized_pnl < 0 else 0
        if direction == target:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _net_profit(result: BacktestResult) -> float:
    if not result.equity_curve:
        return 0.0
    return result.equity_curve[-1].equity - float(result.metadata["initial_capital"])


def _ranking_number(value: object, *, missing: float) -> float:
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, float) and math.isinf(value):
        return value
    return missing


def _reward_risk(payload: Mapping[str, ImmutableValue]) -> float | None:
    direct = _optional_number(payload.get("reward_risk"))
    if direct is not None:
        return direct
    direct = _optional_number(payload.get("reward_risk_ratio"))
    if direct is not None:
        return direct
    direct = _optional_number(payload.get("rr"))
    if direct is not None:
        return direct
    risk_amount = _optional_number(payload.get("risk_amount"))
    realized_pnl = _optional_number(payload.get("realized_pnl"))
    if risk_amount is not None and risk_amount > 0 and realized_pnl is not None:
        return realized_pnl / risk_amount
    return None


def _payload_timestamp(event: EventEnvelope, field_name: str) -> datetime:
    value = event.payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Backtest evidence requires {field_name}.")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"Backtest {field_name} must be timezone-aware.")
    return timestamp


def _required_number(value: ImmutableValue | object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise ValueError(f"Backtest {field_name} must be a finite number.")
    return float(value)


def _positive_number(value: ImmutableValue | object, field_name: str) -> float:
    number = _required_number(value, field_name)
    if number <= 0:
        raise ValueError(f"Backtest {field_name} must be positive.")
    return number


def _nonnegative_number(value: ImmutableValue | object, field_name: str) -> float:
    number = _required_number(value, field_name)
    if number < 0:
        raise ValueError(f"Backtest {field_name} must not be negative.")
    return number


def _optional_number(value: ImmutableValue | object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        return None
    return float(value)


def _text(value: ImmutableValue | object, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default
