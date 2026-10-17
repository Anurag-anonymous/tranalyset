# Risk Plugin

The Risk Plugin assesses `TradeRequested` intents before simulated submission. It owns risk
decisions and calculated sizing/protective levels; it does not place orders. Approvals include the
paper order parameters, quantity, entry reference, and risk attribution required by the Paper
Trading Engine.

## Subscriptions

The plugin subscribes to:

- `TradeRequested`
- `TradeUpdated`
- `PortfolioUpdated`
- `PaperTradeClosed`
- `PaperRiskLimitEnforced`
- trade-health, probability, and market-context evidence

Requests are assessed once per trade ID. Closed trades are not re-assessed. An actionable request
is rejected if risk denies it or a positive position size cannot be calculated. Paper execution
accepts only an approval sourced from `TradeRequested`; a rejection cannot submit an order.
Portfolio exposure is normalized to a fraction of capital before applying the fractional exposure
limit. Period-specific P&L and capital are consumed from Portfolio Engine snapshots. A paper risk
halt prevents subsequent requests from being approved.

## Published Events

The plugin registers and publishes:

- `RiskApproved`
- `RiskRejected`
- `PositionSizeCalculated`
- `StopLossUpdated`
- `TakeProfitUpdated`
- `TrailingStopUpdated`

Publishing is exposed through explicit methods:

- `publish_risk_approved(...)`
- `publish_risk_rejected(...)`
- `publish_position_size_calculated(...)`
- `publish_stop_loss_updated(...)`

Risk decisions are separate from strategy configuration and candidate ranking. Approval payloads
carry parameters for simulated orders only; they do not authorize or reach a real broker. Risk
state is cleared when the paper trade closes.
