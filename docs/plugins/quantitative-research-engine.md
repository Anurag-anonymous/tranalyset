# Quantitative Research Engine

The legacy strategy-evaluation and parameter-sweep APIs accept event windows but do not execute a
strategy against them. They therefore return `status: "not_evaluated"` with empty metrics, rankings,
and distributions. A parameter grid alone cannot produce performance results.

The strategy laboratory calculates results only from validated `PaperTradeClosed` ledger entries
whose strategy attribution matches the supplied research input. It requires trades in training,
validation, and out-of-sample periods before a configuration can enter rankings. Empty periods
report a zero trade count and unavailable metrics; they are not ranked. Configuration and
performance are reported separately.

For evaluated laboratory inputs:

- net profit and expectancy use net `realized_pnl` from closed trades;
- total return uses initial capital and cumulative realized P&L;
- win rate counts positive realized-P&L trades over all closed trades;
- profit factor is gross gains divided by absolute gross losses; it is infinite when gains exist
  without losses and unavailable when there are neither gains nor losses;
- drawdown uses the realized-trade equity curve beginning at initial capital;
- Sharpe and Sortino use per-trade equity returns without annualization. They are unavailable
  with fewer than two observations, zero volatility, or an invalid equity denominator;
- Monte Carlo permutes the order of recorded out-of-sample trade P&Ls to show path-dependent outcomes;
  it is reproducible for a fixed seed and does not imply significance or validity.

The laboratory records dataset identity, date range, strategy version, configuration, execution
assumptions, and all three evaluation periods. Missing strategy version or cost assumptions are
explicitly recorded as unknown/empty. Execution-stress estimates are unavailable until complete
fill-level notionals are present. These calculations do not claim statistical significance or
out-of-sample validity beyond the supplied split and observed evidence.
