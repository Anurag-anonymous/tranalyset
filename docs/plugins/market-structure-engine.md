# Market Structure Engine

The Market Structure Engine is an observational plugin. It classifies structure and liquidity
conditions, but it does not generate trade signals or recommendations.

## Subscriptions

The plugin subscribes to:

- `CandleClosed`
- `TimeframeCandleClosed`
- `IndicatorsUpdated`
- `FeaturesUpdated`
- `PatternDetected`
- `PatternUpdated`
- `PatternInvalidated`
- `MarketRegimeChanged`

It uses these events as evidence for multi-timeframe structure analysis.

## Published Events

The plugin publishes:

- `MarketStructureUpdated`
- `StructureBreak`
- `CharacterChanged`
- `LiquidityDetected`

These events describe structural state, confidence, and supporting evidence only.

## Structural Evidence

The engine evaluates:

- Swing highs and swing lows
- Higher highs, higher lows, lower highs, and lower lows
- Break of structure and change of character
- Equal highs and equal lows
- Liquidity grabs and liquidity sweeps
- Order blocks, breaker blocks, and mitigation blocks
- Fair value gaps and imbalances
- Premium and discount zones

The plugin remains non-prescriptive. It reports evidence and confidence, but it never emits a
trading recommendation.
