# Market Data Plugin

The Market Data Plugin is a boundary plugin. It does not analyze, score, or predict markets.

## Subscription

The plugin subscribes to:

- `ApplicationStarted`

The subscription marks the plugin as application-ready. It does not synthesize market data.

## Public Events

The plugin registers the market-data pipeline contracts. It publishes:

- `TickReceived`
- `CandleClosed`
- `MarketOpened`
- `MarketClosed`
- `ConnectionLost`
- `ConnectionRestored`

The timeframe aggregation infrastructure emits `TimeframeCandleClosed`.

Publishing is exposed through explicit methods such as `publish_tick_received(...)` and
`publish_connection_lost(...)`. Upstream adapter plugins or infrastructure can call those methods
after they receive real market data or connection state from an external source.

## Canonical Event Contracts

`TickReceived` uses `TickReceivedPayload`: `symbol`, `price`, `size`, and the timezone-aware
`received_at` timestamp, with `venue` and `side` when supplied by the source.

`CandleClosed` and `TimeframeCandleClosed` use `CandleClosedPayload`: `symbol`, `timeframe`,
timezone-aware `opened_at` and `closed_at` timestamps, OHLC prices, `volume`, and `venue` when
known. The timeframe aggregation service consumes the first contract and emits the second using
the same payload schema.

`MarketOpened` and `MarketClosed` use `MarketSessionPayload`: `market`, timezone-aware
`occurred_at`, and `venue` when known. The calendar service both publishes and consumes these
contracts to keep session state synchronized.

Historical replay publishes these exact contracts through the ordinary event bus. Lowercase
transport-specific event names are not supported.

## Persistence Ordering

The Market Data Plugin validates a source payload, persists its domain record through the shared
`MarketDataRepository`, and only then publishes the event. Historical replay follows the same
repository idempotency semantics before it publishes. The timeframe aggregator persists each
finalized derived candle before it emits `TimeframeCandleClosed`.

The repository uses a deterministic digest of the immutable market record as its idempotency key,
so a retried identical tick or candle is not stored twice. A persistence failure is propagated to
the caller and prevents the live plugin from publishing the corresponding event.

## Boundaries

This plugin owns market data event contracts and publication mechanics only. It contains no
analysis, no strategy, no signal generation, and no risk decisions.
