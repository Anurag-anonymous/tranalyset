# AI Reasoning Engine Plugin

The AI Reasoning Engine explains important system decisions from received event evidence. It does
not generate trading signals, approve risk, open trades, close trades, or infer missing facts.

## Evidence Boundary

Each explanation is generated from:

- the source event envelope,
- the source event payload,
- the source event metadata,
- optional retrieved evidence supplied by a future RAG adapter.

If a field is absent from the received evidence, the plugin does not invent it.

## Subscriptions

The plugin subscribes to major decision and state events:

- `TradeHealthUpdated`
- `ConfidenceChanged`
- `TradeStrengthChanged`
- `RiskApproved`
- `RiskRejected`
- `PositionSizeCalculated`
- `StopLossUpdated`
- `TradeSignalGenerated`
- `TrailingStopUpdated`
- `MarketRegimeChanged`
- `TrendConfirmed`
- `TrendWeakened`
- `VolatilityChanged`
- `LiquidityChanged`
- `MomentumStateChanged`
- `PatternDetected`
- `PatternInvalidated`

## Published Events

The plugin registers and publishes:

- `ReasoningGenerated`

## LLM Integration

The default provider is deterministic and local. External LLM support is provided through the
`ReasoningProvider` protocol. A provider receives a `ReasoningRequest` containing only the event
evidence and optional retrieved context, and returns a `ReasoningResponse`.

## Future RAG Integration

Future retrieval can be integrated through the `EvidenceRetriever` protocol. Retrieved references
are attached to the evidence payload and surfaced in the explanation limitations.
