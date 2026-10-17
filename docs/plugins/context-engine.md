# Context Engine Plugin

The Context Engine combines market evidence into an immutable contextual snapshot. It never
recommends trades, opens trades, or manages risk directly.

## Subscriptions

The plugin subscribes to:

- `IndicatorsUpdated`
- `FeaturesUpdated`
- `PatternDetected`
- `PatternUpdated`
- `PatternInvalidated`
- `MarketStructureUpdated`
- `StructureBreak`
- `CharacterChanged`
- `LiquidityDetected`
- `NewsAnalyzed`
- `MarketSentimentChanged`
- `MarketRegimeChanged`
- `TrendConfirmed`
- `TrendWeakened`
- `VolatilityChanged`
- `LiquidityChanged`
- `MomentumStateChanged`

## Published Events

The plugin registers and publishes:

- `MarketContextUpdated`

## Context Dimensions

The engine synthesizes:

- trend context
- momentum context
- volatility context
- volume context
- liquidity context
- market context
- sector context
- session context
- risk context

## Output

Each emitted snapshot includes confidence, quality, supporting evidence, and the source event type
that triggered the update. The payload contains context only and must not contain trade actions or
recommendations.

## Retention

The service keeps only the latest component values and snapshot for each tracked context key.
`max_tracked_keys` defaults to 5,000; least-recently-used keys and their component books and
snapshots are evicted together.
