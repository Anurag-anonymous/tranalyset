# Event-Driven Microkernel Architecture

## Goals

Tranalyset is organized as a microkernel with replaceable plugins. The kernel provides lifecycle,
dependency management, dependency injection, and event routing. Plugins provide features.

The architecture is intentionally infrastructure-first:

- The system evaluates probabilities rather than predicting markets.
- Every market event is evidence represented as an immutable event.
- Recommendations must be explainable by the events and metadata that produced them.
- Risk controls are modeled as independent plugins that can override opportunity plugins.
- Plugins never call each other directly.

## Folder Structure

```text
src/tranalyset/
  api/
    app.py                  FastAPI composition root.
  contracts/
    events.py               Immutable event envelope and event type value object.
    plugins.py              Plugin metadata, lifecycle state, and public protocols.
  domain/                 Pure Domain Layer bounded context.
    common/               Shared value objects and domain enums.
    market/               Instruments, quotes, ticks, candles, sessions, order books.
    trading/              Orders and executed trades.
    portfolio/            Positions, portfolios, PnL, valuation logic.
    analytics/            Market metrics, indicator snapshots, performance metrics.
    risk/                 Exposures, limits, and risk assessments.
    strategy/             Strategy identifiers, definitions, and signals.
    services/             Pure domain calculators and analyzers.
    factories/            Safe constructors for complex domain objects.
    events.py             Internal domain events before transport adaptation.
    exceptions.py         Domain validation exception hierarchy.
    ubiquitous_language/   Canonical business terms and glossary rendering.
  kernel/
    container.py            Minimal dependency injection container.
    event_bus.py            Async publish/subscribe event bus.
    event_catalog.py        Registered event catalog with snapshot support.
    kernel.py               Microkernel lifecycle coordinator.
    loader.py               Plugin loader contracts and import-based loader.
    plugin_registry.py      Plugin registration and dependency ordering.
  market_data/
    calendar.py             Market calendar service for session tracking and events.
    corporate_actions.py    Corporate actions service for adjusted historical data.
    models.py               Tick, Candle, InstrumentMetadata, Calendar dataclasses.
    providers.py            Historical data provider abstraction (CSV, Parquet, DB, APIs).
    quality.py              Data quality engine for validating incoming market data.
    repository.py           Market data repository and storage backends.
  plugins/                Feature modules (trading, risk, signals, feature_engine, indicator_engine, market_structure_engine, etc.)
  plugin_sdk/              Base classes for plugin authors.
  strategy_sdk/            SDK for building trading strategies.
  tests/
    unit/                   Architecture-level unit tests.
    benchmarks/             Performance benchmarks.
  docs/
    architecture.md         Design decisions and extension rules.
    optimization-report.md  Performance and memory optimization plan.
    BENCHMARKS.md           Performance measurement results.
```

## Domain Layer

The Domain Layer represents the core business concepts of the trading system. It is a completely
independent bounded context and is the single source of truth for business objects used by plugins,
strategy code, and application services.

### Principles

- **Independence**: It has zero dependencies on FastAPI, kernel, plugins, event bus, broker APIs,
  databases, LLMs, or UI.
- **Immutability**: Domain objects use `@dataclass(frozen=True, slots=True)` where appropriate.
- **Purity**: Domain objects contain business invariants and business calculations only.
- **No transport logic**: Domain objects do not expose event payload, JSON, DTO, broker, database,
  or API serialization methods.
- **Strong typing**: Value objects such as `Symbol`, `Exchange`, `Currency`, `Price`, `Quantity`,
  `Money`, `Ratio`, and `Timeframe` prevent primitive obsession at the domain boundary.
- **No mutable global state**: Domain behavior is expressed as pure methods that return new values.
- **Hashable equality**: Domain objects implement value equality and hashing, including immutable
  mapping-backed models.

### Components

- `common`: shared value objects and domain enums.
- `market`: instruments, quotes, ticks, candles, order books, trading sessions, calendars, and market
  state/regime concepts.
- `trading`: orders and executed trades, including order invariants and cash-flow calculations.
- `portfolio`: positions and portfolios, including PnL, valuation, and immutable trade application.
- `analytics`: market metrics, indicator snapshots, and performance metrics.
- `risk`: exposures, risk limits, and risk assessments.
- `strategy`: strategy identifiers, definitions, and domain-level signals.
- `services`: pure calculators and analyzers that operate only on domain models.
- `factories`: validated constructors for complex domain objects.
- `events`: internal business events used before transport conversion.
- `exceptions`: shared domain validation errors.
- `ubiquitous_language`: canonical terminology, relationships, and glossary generation.

Plugins should exchange Domain Models instead of raw dictionaries when both publisher and
subscriber share the domain contract. Event envelopes remain transport contracts and must not
contain business logic.

The `market_structure_engine` plugin consumes feature, indicator, and pattern evidence to classify
structure, liquidity, and character changes without emitting trading signals.

The `context_engine` plugin combines indicators, features, patterns, market structure, market
state, and news into a contextual snapshot. It publishes `MarketContextUpdated` and never emits
trade recommendations.

The `probability_engine` plugin consumes contextual evidence and estimates trade-success
probabilities. It publishes `ProbabilityUpdated` and `ConfidenceUpdated` and never predicts prices
or generates entries or exits.

The `pattern_intelligence_engine` plugin evaluates closed candles with indicator, feature, and
market-regime context. It owns pattern detection and publishes `PatternDetected`, `PatternUpdated`,
and `PatternInvalidated`. Its repository, indicator cache, and timeframe aggregator are resolved
from kernel infrastructure; its declared plugin dependencies are started before it.

### Event Ordering and Recovery

The event bus preserves FIFO enqueue order for events sharing a semantic stream. Trade-, portfolio-,
position-, and order-scoped events use their corresponding payload identifier; market events use
exchange, symbol, and timeframe. Events without one of these keys are independent. Priority orders
ready streams, but does not reorder events inside a stream. Each stream is processed by one worker at
a time, so a slow handler or its retries block only that stream.

Dead letters are retained in a bounded in-memory queue (1,000 by default). Failures for market-data
and risk lifecycle event types are tagged critical, counted separately, and logged at critical
severity. A retained failure can be redriven once to its original subscription without replaying
other subscribers. Dead letters are not durable: eviction or process restart removes them.

The kernel starts and stops the timeframe aggregator and market-calendar service as shared
infrastructure, including during cleanup after a plugin startup failure.

### State Retention

Stateful plugins use bounded recency-based retention. The Signal Orchestrator retains up to 10,000
candidate keys and 20,000 signal IDs by default. The Context Engine retains 5,000 context keys; the
Probability Engine retains 10,000 evidence keys; and Pattern Intelligence retains 5,000 symbol,
exchange, and timeframe series. Each limit is configurable through its plugin configuration.
Eviction removes the associated in-memory evidence/snapshots; it does not write an archive.

## Kernel
... (rest of the original file)
