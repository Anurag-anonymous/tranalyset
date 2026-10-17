# News Plugin

The News Plugin is an event boundary for news collection and sentiment state. It does not fetch
external news, generate recommendations, place orders, or make trading decisions.

## Subscription

The plugin subscribes to:

- `TimerElapsed`

The timer event is scheduling evidence only. Concrete news adapters and analysis engines can be
added later behind this contract.

## Published Events

The plugin registers and publishes:

- `NewsReceived`
- `NewsAnalyzed`
- `MarketSentimentChanged`

Publishing is exposed through explicit methods:

- `publish_news_received(...)`
- `publish_news_analyzed(...)`
- `publish_market_sentiment_changed(...)`

Payloads describe news and sentiment evidence only. They must not contain order intent,
recommendations, or execution instructions.
