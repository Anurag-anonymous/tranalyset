# Trade Health Engine Plugin

The Trade Health Engine continuously calculates the quality of an existing or potential trade from
evidence events. It never generates entries, exits, orders, recommendations, or broker actions.

## Subscriptions

The plugin subscribes to:

- `IndicatorsUpdated`
- `PatternDetected`
- `MarketRegimeChanged`
- `TrendConfirmed`
- `TrendWeakened`
- `NewsAnalyzed`
- `RiskApproved`
- `MomentumStateChanged`

## Scoring

The engine calculates a `Trade Health Score` from `0` to `100`.

Factors:

- trend
- momentum
- volume
- volatility
- structure
- liquidity
- pattern quality
- sector strength
- market strength
- news
- sentiment
- risk

Weights are configurable through plugin configuration:

```python
{"weights": {"risk": 2.0, "trend": 1.5}}
```

Unspecified factors use defaults. Unknown factors or negative weights fail configuration.

## Confidence

Every score includes:

- `confidence_lower`
- `confidence_upper`
- factor scores
- applied weights
- evidence count
- explanations

Confidence intervals narrow as more factors receive evidence.

## Published Events

The plugin registers and publishes:

- `TradeHealthUpdated`
- `ConfidenceChanged`
- `TradeStrengthChanged`

Payloads describe trade quality only. They must not contain entry instructions, exit instructions,
order intent, position actions, or recommendations.
