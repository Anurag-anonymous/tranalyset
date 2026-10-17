# Probability Engine Plugin

The Probability Engine continuously estimates trade-success probability from accumulated evidence.
It never predicts prices and never emits entries or exits.

## Subscriptions

The plugin subscribes to:

- `MarketContextUpdated`
- `TradeHealthUpdated`
- `PatternDetected`
- `PatternUpdated`
- `PatternInvalidated`
- `IndicatorsUpdated`
- `MarketStructureUpdated`
- `StructureBreak`
- `CharacterChanged`
- `LiquidityDetected`
- `RiskApproved`
- `RiskRejected`
- `PositionSizeCalculated`
- `StopLossUpdated`

## Published Events

The plugin registers and publishes:

- `ProbabilityUpdated`
- `ConfidenceUpdated`

## Outputs

The engine estimates:

- bullish probability
- bearish probability
- trend continuation probability
- trend reversal probability
- breakout probability
- breakdown probability
- probability distribution
- confidence interval
- reasoning metadata

## Design Notes

The default model uses Bayesian-style fractional updates over accumulated evidence. The service is
model-driven so a future ML implementation can be registered without changing the plugin contract.

The `history_limit` setting bounds per-trade source events and notes. `max_tracked_keys` defaults
to 10,000 and bounds evidence and cached snapshots by recent trade activity; evicted evidence is
not archived.
