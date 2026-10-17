# Technical Analysis Plugin

The Technical Analysis Plugin is an event-bound analysis component. It does not make trading
decisions, create recommendations, place orders, size positions, or override risk controls.

## Subscriptions

The plugin subscribes to:

- `TickReceived`
- `CandleClosed`

Handlers record that market data evidence has arrived. Indicator calculation engines can be added
behind this contract later, but this plugin does not infer trades.

## Published Events

The plugin registers and publishes:

- `IndicatorsUpdated`
- `TrendChanged`
- `MomentumChanged`
- `StructureChanged`

Publishing is exposed through explicit methods:

- `publish_indicators_updated(...)`
- `publish_trend_changed(...)`
- `publish_momentum_changed(...)`
- `publish_structure_changed(...)`

Payloads describe analysis state only. They must not contain order intent, position sizing,
recommendations, or trade actions.
