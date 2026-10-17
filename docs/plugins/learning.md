# Learning Plugin

The Learning Plugin observes completed trade lifecycle evidence and publishes learning-state
events. It does not activate strategies, place orders, bypass risk, or make trading decisions.

## Subscriptions

The plugin subscribes to:

- `TradeClosed`
- `TradeModified`
- `TradeOpened`

Handlers record that lifecycle evidence has arrived.

## Published Events

The plugin registers and publishes:

- `LearningUpdated`
- `StrategyImproved`

Publishing is exposed through explicit methods:

- `publish_learning_updated(...)`
- `publish_strategy_improved(...)`

Payloads describe model or strategy improvement evidence only. They must not contain activation
instructions, order intent, or recommendations.
