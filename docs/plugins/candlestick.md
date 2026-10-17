# Candlestick Plugin

The Candlestick Plugin is an observational pattern-event plugin. It does not make trading
decisions, emit recommendations, place orders, or size positions.

## Subscriptions

The plugin subscribes to:

- `CandleClosed`
- `IndicatorsUpdated`

Handlers record that candle and indicator evidence has arrived.

## Published Events

The plugin registers and publishes:

- `PatternDetected`
- `PatternInvalidated`

Publishing is exposed through explicit methods:

- `publish_pattern_detected(...)`
- `publish_pattern_invalidated(...)`

Payloads describe pattern state and supporting evidence only. They must not contain trade actions
or execution intent.
