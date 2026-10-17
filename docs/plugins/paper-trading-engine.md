# Paper Trading Engine Plugin

The Paper Trading Engine owns simulated orders, fills, positions, cash, equity, and the realized
trade ledger. It is paper-only: it never connects to a broker or places real orders.

## Subscriptions

Entry orders are submitted only from `RiskApproved` events whose `source_event_type` is
`TradeRequested`. Candidates and `TradeOpened` facts are not order-submission commands. Risk
rejections do not create orders. The plugin also subscribes to:

- `TradeModified`
- `TradeClosed`
- `TickReceived`
- `CandleClosed`
- `StopLossUpdated`
- `TakeProfitUpdated`
- `TrailingStopUpdated`
- `RiskRejected`

## Supported Simulation

- market, limit, and stop entry orders;
- partial fills and partial exits;
- fees and slippage on simulated fills;
- stop-loss, take-profit, and trailing-stop exits;
- paper risk-limit liquidation.

Market orders fill immediately at the approved reference price adjusted for configured slippage.
Pending orders and protective exits are evaluated against tick and candle-close data. Rejections,
fills, and exits are recorded with execution evidence. Entry affordability includes configured
slippage and commissions. Stop exits, including trailing stops, account for adverse candle gaps.
`TradeOpened`, `TradeModified`, and
`TradeClosed` are emitted only after corresponding simulated fills or position changes.

`PaperPortfolioUpdated` is the authoritative snapshot for cash, equity, filled positions, and
closed-trade history. Portfolio consumers should reconcile to this snapshot rather than requested
quantities or risk approvals.

## Published Events

The plugin publishes:

- `PaperTradeUpdated`
- `PaperTradeClosed`
- `PaperPortfolioUpdated`
- `PaperExecutionRecorded`
- `PaperRiskLimitEnforced`

Payloads describe simulated state only. They must not contain live broker instructions. Execution
is long-only and depends on supplied market data; it is not a broker fill simulation.
