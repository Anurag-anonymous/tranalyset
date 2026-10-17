from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class Position:
    trade_id: str
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    sector: str
    correlation_group: str
    realized_pnl: float = 0.0

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.quantity

    def to_payload(self) -> dict[str, float | str]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "sector": self.sector,
            "correlation_group": self.correlation_group,
        }


@dataclass(frozen=True, slots=True)
class ClosedPosition:
    trade_id: str
    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    realized_pnl: float
    sector: str

    def to_payload(self) -> dict[str, float | str]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "realized_pnl": self.realized_pnl,
            "sector": self.sector,
        }


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    capital: float
    cash: float
    open_positions: tuple[dict[str, float | str], ...]
    closed_positions: tuple[dict[str, float | str], ...]
    exposure: float
    sector_exposure: dict[str, float]
    correlation: float
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    drawdown: float
    portfolio_risk: float
    peak_capital: float


@dataclass(slots=True)
class PortfolioState:
    initial_capital: float = 100_000.0
    cash: float = 100_000.0
    peak_capital: float = 100_000.0
    open_positions: dict[str, Position] = field(default_factory=dict)
    closed_positions: list[ClosedPosition] = field(default_factory=list)
    risk_by_trade: dict[str, float] = field(default_factory=dict)
    equity_history: list[tuple[datetime, float]] = field(default_factory=list)
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    monthly_pnl: float = 0.0

    def record_equity(self, occurred_at: datetime, capital: float) -> None:
        if occurred_at.tzinfo is None:
            raise ValueError("Portfolio equity timestamps must be timezone-aware.")
        if self.equity_history and occurred_at < self.equity_history[-1][0]:
            raise ValueError("Portfolio equity snapshots must be chronological.")

        self.equity_history.append((occurred_at, capital))
        daily_start = occurred_at.replace(hour=0, minute=0, second=0, microsecond=0)
        weekly_start = daily_start - timedelta(days=occurred_at.weekday())
        monthly_start = daily_start.replace(day=1)
        self.daily_pnl = capital - self._period_opening_equity(daily_start)
        self.weekly_pnl = capital - self._period_opening_equity(weekly_start)
        self.monthly_pnl = capital - self._period_opening_equity(monthly_start)

    def _period_opening_equity(self, period_start: datetime) -> float:
        earlier = [
            equity
            for occurred_at, equity in self.equity_history[:-1]
            if occurred_at < period_start
        ]
        return earlier[-1] if earlier else self.initial_capital

    def snapshot(self) -> PortfolioSnapshot:
        open_payload = tuple(position.to_payload() for position in self.open_positions.values())
        closed_payload = tuple(position.to_payload() for position in self.closed_positions)
        exposure = sum(abs(position.market_value) for position in self.open_positions.values())
        capital = self.cash + sum(position.market_value for position in self.open_positions.values())
        self.peak_capital = max(self.peak_capital, capital)
        sector_exposure = self._sector_exposure(exposure)
        correlation = self._correlation(exposure)
        drawdown = 0.0 if self.peak_capital <= 0 else max(0.0, (self.peak_capital - capital) / self.peak_capital)
        portfolio_risk = self._portfolio_risk(capital)
        return PortfolioSnapshot(
            capital=round(capital, 4),
            cash=round(self.cash, 4),
            open_positions=open_payload,
            closed_positions=closed_payload,
            exposure=round(exposure, 4),
            sector_exposure={key: round(value, 4) for key, value in sector_exposure.items()},
            correlation=round(correlation, 4),
            daily_pnl=round(self.daily_pnl, 4),
            weekly_pnl=round(self.weekly_pnl, 4),
            monthly_pnl=round(self.monthly_pnl, 4),
            drawdown=round(drawdown, 6),
            portfolio_risk=round(portfolio_risk, 6),
            peak_capital=round(self.peak_capital, 4),
        )

    def _sector_exposure(self, exposure: float) -> dict[str, float]:
        values: dict[str, float] = defaultdict(float)
        for position in self.open_positions.values():
            values[position.sector] += abs(position.market_value)
        if exposure <= 0:
            return dict(values)
        return {sector: value / exposure for sector, value in values.items()}

    def _correlation(self, exposure: float) -> float:
        if exposure <= 0:
            return 0.0
        groups: dict[str, float] = defaultdict(float)
        for position in self.open_positions.values():
            groups[position.correlation_group] += abs(position.market_value)
        if not groups:
            return 0.0
        return max(groups.values()) / exposure

    def _portfolio_risk(self, capital: float) -> float:
        if capital <= 0:
            return 1.0
        approved_risk = sum(max(0.0, value) for value in self.risk_by_trade.values())
        exposure = sum(abs(position.market_value) for position in self.open_positions.values())
        risk_ratio = approved_risk / capital
        exposure_ratio = exposure / capital
        return min(1.0, risk_ratio + exposure_ratio * 0.25)
