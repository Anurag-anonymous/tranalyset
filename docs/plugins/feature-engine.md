# Feature Engineering Engine

The Feature Engineering Engine is a pure feature-generation plugin in the event-driven
microkernel architecture.

## Responsibility

- Subscribe to `TickReceived`, `CandleClosed`, and `TimeframeCandleClosed`.
- Transform market observations into reusable quantitative feature sets.
- Cache generated feature sets for downstream consumers.
- Publish `FeaturesUpdated`.

## Scope

The engine computes market structure and price-action features such as:

- Price change and returns
- Gap size and direction
- Candle body and wick measures
- True range and ATR components
- Rolling descriptive statistics
- VWAP, EMA, and SMA distance features
- Volume and liquidity features
- Session position and time-to-close context
- Momentum, trend, support, and resistance distances

## Boundaries

The engine does not:

- Generate trading signals
- Manage risk
- Open or close trades
- Publish recommendations

## Dependencies

The engine depends on shared infrastructure only:

- Market Data Repository
- Timeframe Aggregator
- Indicator Cache
- Domain models

## Output

Generated features are emitted as immutable feature sets and cached in memory for reuse by
downstream plugins.
