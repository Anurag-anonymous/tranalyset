# Indicator Engine

The Indicator Engine is the streaming technical-indicator layer in the microkernel.

## Responsibility

- Subscribe to `TickReceived`, `CandleClosed`, `TimeframeCandleClosed`, and `FeaturesUpdated`.
- Incrementally calculate technical indicators from market data.
- Cache calculated indicator values.
- Publish `IndicatorCalculated`, `IndicatorInvalidated`, and `IndicatorsUpdated`.

## Supported Indicators

- EMA
- SMA
- WMA
- VWAP
- RSI
- MACD
- ATR
- ADX
- CCI
- ROC
- MFI
- OBV
- CMF
- Supertrend
- Ichimoku
- Donchian
- Keltner
- Bollinger
- Stochastic
- Williams %R
- Parabolic SAR
- Volume Profile

## Boundaries

The engine does not:

- Generate trading signals
- Manage risk
- Open or close trades
- Recompute historical data unnecessarily

## Dependencies

The engine depends on shared infrastructure and upstream context:

- Market Data Repository
- Feature Engine
- Indicator Cache
- Multi-timeframe market events

## Output

Each calculation produces immutable event payloads and cached indicator values keyed by symbol,
exchange, timeframe, indicator name, and version.
