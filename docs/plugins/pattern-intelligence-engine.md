# Pattern Intelligence Engine Plugin

The Pattern Intelligence Engine detects and updates chart patterns from real closed candles and
their available market context. It does not create trade recommendations.

## Dependencies and inputs

The plugin depends on `market_data`, `feature_engine`, `indicator_engine`, and
`market_state_engine`. The kernel starts those plugins before this engine and provides the shared
market-data repository, indicator cache, and timeframe aggregation service.

It consumes `CandleClosed`, `TimeframeCandleClosed`, `IndicatorsUpdated`, `FeaturesUpdated`, and
`MarketRegimeChanged`. Candle observations are persisted to the market-data repository before the
service evaluates the configured lookback.

## Published events

- `PatternDetected` — a newly active pattern.
- `PatternUpdated` — a material confidence or strength update.
- `PatternInvalidated` — a pattern invalidated by later observations.

## Retention

The engine retains bounded candle, indicator, feature, regime, and active-pattern state per
symbol/exchange/timeframe series. `max_tracked_series` defaults to 5,000; least-recently-used series
are evicted together with their associated in-memory context and active-pattern state. Per-series
candle history is separately limited by `history_limit`.
