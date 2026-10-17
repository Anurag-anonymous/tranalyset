# Trade Manager Plugin

The Trade Manager coordinates trade intent and logical lifecycle state. It does not execute
orders, choose entries, calculate risk, or make trading decisions. An accepted entry candidate is
intent only; the manager converts it to a deterministic `TradeRequested` event for Risk.

## Subscriptions

The plugin subscribes to:

- `TradeSignalGenerated`
- `AcceptedCandidate`
- `RiskApproved`
- `RiskRejected`
- `PaperExecutionRecorded`
- `PaperTradeClosed`
- `TradeOpened`
- `TradeModified`
- `TradeClosed`
- `TradeHealthUpdated`

Only accepted entry candidates with a valid long-side entry reference produce a request. Duplicate
signal IDs are ignored. At most one requested, approved, or open trade per symbol/timeframe is
allowed; a later accepted candidate is not submitted until that trade is rejected or closed.
Logical state advances through requested, approved/rejected, open, partially closed, and closed
states based on risk decisions and paper execution facts.

## Event Contracts

The production candidate path publishes:

- `TradeRequested`

`TradeOpened`, `TradeModified`, and `TradeClosed` describe completed lifecycle transitions, not
candidate acceptance. Paper Trading publishes those facts after simulated execution; it owns actual
order, fill, and position state. Portfolio state is reconciled from paper snapshots.
`TrailingStopUpdated` uses the shared Risk event contract.

Explicit lifecycle event publication helpers remain available to callers:

- `publish_trade_opened(...)`
- `publish_trade_modified(...)`
- `publish_trade_closed(...)`
- `publish_trailing_stop_updated(...)`

Payloads describe lifecycle state only. They must not contain broker order placement instructions
or strategy recommendations.
