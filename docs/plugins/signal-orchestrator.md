# Signal Orchestrator Plugin

The Signal Orchestrator aggregates candidate signals from strategy plugins and produces a ranked set of final candidate decisions.
It does not create trading strategies or replace strategy signal generation.

## Responsibilities

- Collect `TradeSignalGenerated` candidate events from every strategy plugin.
- Deduplicate repeated candidate signals.
- Rank candidates by confidence, strategy performance, market regime, and vote strength.
- Resolve conflicting candidates for the same symbol and timeframe.
- Publish finalized signal decisions with acceptance and rejection metadata.

## Supported Features

- Multiple strategies.
- Voting when the same candidate is proposed by multiple sources.
- Weighted confidence using historical strategy performance.
- Market regime weighting.
- Multiple timeframes.

## Published Events

- `TradeSignalGenerated` — final accepted signal candidates are re-published for downstream consumers.
- `WatchCandidateGenerated` — watch candidate notifications.
- `AcceptedCandidate` — accepted candidate metadata and ranking details.
- `RejectedCandidate` — rejected candidate metadata and ranking details.

## Notes

The orchestrator expects strategy plugins to publish initial candidate events using the shared `TradeSignalGenerated` event type.
It enriches those signals with orchestration decisions and publishes accepted or rejected outcomes separately.

## Retention

`max_candidates` bounds ranked candidate keys and their groups (10,000 by default).
`max_seen_signal_ids` bounds duplicate suppression history (20,000 by default). The oldest
retained entries are evicted first; a duplicate older than that retained window may be accepted
again.
