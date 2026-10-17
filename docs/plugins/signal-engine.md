# Signal Engine Plugin

The Signal Engine aggregates evidence from plugins and generates candidate trading opportunities.
It never opens trades, modifies trades, calculates risk, places orders, or bypasses the Risk Plugin.

## Subscriptions

The plugin subscribes to:

- `TradeHealthUpdated`
- `MarketRegimeChanged`
- `PatternDetected`
- `IndicatorsUpdated`
- `NewsAnalyzed`
- `RiskApproved`

## Candidates

The engine can generate:

- `TradeSignalCandidate`
- `WatchCandidate`
- `EntryCandidate`
- `ExitCandidate`
- `PartialExitCandidate`
- `TrailCandidate`

Every candidate includes:

- confidence,
- reasoning,
- supporting evidence,
- reward-risk estimate.

The reward-risk estimate is an opportunity estimate from aggregated evidence. It is not position
risk, approval, sizing, or a substitute for the Risk Plugin.

## Published Events

The plugin registers and publishes:

- `TradeSignalGenerated`

Payloads describe candidates only. They must not contain order placement instructions, trade
mutation instructions, broker actions, or risk approval decisions.
