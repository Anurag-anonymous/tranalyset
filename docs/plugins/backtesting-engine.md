# Backtesting Engine Plugin

The Backtesting Engine replays historical events through the same asynchronous event bus used by
live mode. It does not contain trading logic, generate signals, calculate risk, or place orders.
It observes paper-trading events and derives performance metrics from validated closed-trade
records and portfolio equity points.

## Replay Modes

- historical replay,
- event replay,
- walk-forward replay.

Each replay publishes immutable historical `EventEnvelope` instances into the running kernel after
draining startup events, so initial snapshots cannot contaminate the evaluation period. Historical
entry intents must pass Risk and be filled by Paper Trading; replaying a `TradeOpened` fact alone
does not create a position. Results use only validated simulated fills, closed trades, and equity
snapshots observed inside the replay period.

## Subscriptions

The plugin subscribes to:

- `PaperTradeUpdated`
- `PaperTradeClosed`
- `PaperPortfolioUpdated`

## Published Events

The plugin registers and publishes:

- `BacktestStarted`
- `BacktestProgressUpdated`
- `BacktestCompleted`
- `BacktestResultsExported`

## Metrics

Backtest results include:

- win rate: profitable closed trades divided by all closed trades;
- profit factor: gross realized profit divided by absolute gross realized loss;
- Sharpe ratio: sample-mean daily equity return divided by sample standard deviation, annualized
  by `sqrt(252)`;
- Sortino ratio: mean daily equity return divided by downside RMS of negative daily returns,
  annualized by `sqrt(252)`;
- drawdown: maximum peak-to-trough equity decline, relative to the peak, including initial capital;
- total return: final observed equity divided by initial capital, minus one;
- CAGR: annualized total return when the evaluation period is at least one day;
- expectancy: mean net realized P&L per closed trade;
- daily and monthly returns: changes between observed period-end equity values;
- average reward-to-risk and holding time when the ledger records those observations;
- maximum consecutive wins/losses, loss rate, mean win/loss, average drawdown, recovery factor,
  and Calmar ratio from the closed ledger and equity curve.

Profit factor is infinite when there are gains and no losses, and unavailable when its denominator
is zero with no gains. Ratios requiring returns are unavailable when there are too few observations,
zero volatility, or an invalid return denominator. A result without both closed trades and equity
observations has `status: "not_evaluated"` and is excluded from strategy comparisons. Unsupported
exposure and turnover metrics are reported as unavailable rather than inferred from P&L.

Each result includes a deterministic dataset identity, evaluation date range, initial-capital
configuration, observed fees and slippage, and explicit unknowns for strategy version and
execution assumptions not present in the event data. Replay output is not a claim of statistical
significance or out-of-sample validity.

## Export

Results can be exported as JSON or CSV strings through the plugin API. Export events only describe
the export operation; they do not contain broker credentials or live trading instructions.
