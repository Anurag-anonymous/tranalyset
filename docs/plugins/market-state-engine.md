# Market State Engine Plugin

The Market State Engine classifies current market regime from evidence. It never generates trading
signals, recommendations, orders, position sizing, or execution instructions.

## Subscriptions

The plugin subscribes to:

- `TickReceived`
- `CandleClosed`
- `IndicatorsUpdated`
- `PatternDetected`
- `NewsAnalyzed`
- `MarketSentimentChanged`

## Published Events

The plugin registers and publishes:

- `MarketRegimeChanged`
- `TrendConfirmed`
- `TrendWeakened`
- `VolatilityChanged`
- `LiquidityChanged`
- `MomentumStateChanged`

## Supported Regimes

- `Bull Trend`
- `Bear Trend`
- `Sideways`
- `Range Bound`
- `Breakout`
- `Breakdown`
- `Accumulation`
- `Distribution`
- `High Volatility`
- `Low Volatility`
- `Opening Auction`
- `Closing Auction`
- `Trend Exhaustion`
- `Gap Up`
- `Gap Down`
- `False Breakout`
- `False Breakdown`
- `Strong Momentum`
- `Weak Momentum`

## Evidence

Classification uses only observed evidence:

- price change and gaps,
- volume and liquidity,
- trend score,
- volatility score,
- market structure,
- candlestick context,
- news sentiment,
- market sentiment.

Every emitted event includes confidence and evidence. Payloads describe market state only and must
not contain trade actions or recommendations.
