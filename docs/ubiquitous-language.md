# Ubiquitous Language

This glossary defines the canonical business language used across Tranalyset.
Each term has one authoritative definition in code and in documentation.

## Canonical Terms

### Trade
An executed exchange of an instrument that changes cash and position state.

Aliases: Execution

Relationships:
- derived_from: `Order` - A trade is produced from a filled order.
- part_of: `Position` - Trades open, reduce, or close positions.
- explains: `Risk` - Risk evidence governs whether a trade may proceed.
- uses: `Evidence` - Trade history is evidence for risk, learning, and reasoning.

### Position
The signed quantity of a specific instrument held by a portfolio or account.

Aliases: Holding

Relationships:
- part_of: `Instrument` - A position always refers to a single instrument.
- part_of: `Portfolio` - Positions are held inside portfolios.
- measured_by: `Exposure` - Positions create directional exposure.

### Order
An instruction to buy or sell an instrument under defined execution rules.

Aliases: None

Relationships:
- uses: `Instrument` - Every order targets one instrument.
- derived_from: `Signal` - Orders may be generated from a signal or manual intent.
- constrains: `Risk` - Risk rules validate whether an order can proceed.

### Signal
A structured directional or state-oriented message that expresses trading intent, not execution.

Aliases: None

Relationships:
- produces: `Candidate` - Signals can emerge from candidate evaluation.
- derived_from: `Strategy` - Signals are often produced by strategy logic.
- uses: `Evidence` - Signals should be backed by evidence.

### Candidate
A provisional opportunity or state change that may later become a signal, order, or decision.

Aliases: None

Relationships:
- measured_by: `Confidence` - Candidates carry confidence values.
- measured_by: `Probability` - Candidates express likelihood estimates.
- uses: `Evidence` - Candidates are justified by evidence.
- constrains: `Risk` - Risk determines whether a candidate can advance.

### Confidence
The system's bounded degree of trust in a statement, score, or inference.

Aliases: ConfidenceScore

Relationships:
- consumes: `Probability` - Confidence often wraps probability-like evidence.
- derived_from: `Evidence` - Confidence should be derived from supporting evidence.

### Probability
A bounded likelihood that an event or hypothesis will occur or hold true.

Aliases: None

Relationships:
- supports: `Confidence` - Probability is a common input to confidence.
- derived_from: `Evidence` - Probability estimates are derived from evidence.

### Risk
The possibility of adverse outcome, measured and constrained before opportunity is accepted.

Aliases: None

Relationships:
- measured_by: `Exposure` - Risk is shaped by exposure.
- explains: `Trade Health` - Trade health reflects risk quality.
- constrains: `Portfolio` - Portfolio-level risk overrides local opportunity.

### Exposure
Capital-at-risk or directional market participation attributed to an instrument, sector, or portfolio.

Aliases: None

Relationships:
- derived_from: `Position` - Exposure is derived from positions.
- part_of: `Portfolio` - Exposure is aggregated at the portfolio level.
- measured_by: `Risk` - Risk control is often expressed through exposure limits.

### Market Regime
The current market condition class used to summarize the active environment for decision-making.

Aliases: Regime

Relationships:
- derived_from: `Trend` - Trend is a primary input to regime classification.
- derived_from: `Momentum` - Momentum helps identify the active regime.
- derived_from: `Volatility` - Volatility is a regime-defining feature.
- uses: `Session` - Session context can alter regime interpretation.

### Trend
The directional bias of price over time.

Aliases: None

Relationships:
- supports: `Momentum` - Momentum helps confirm trend strength.
- explains: `Pattern` - Patterns often form around trend structure.

### Momentum
The persistence or acceleration of price movement over a measured period.

Aliases: None

Relationships:
- supports: `Trend` - Momentum helps validate trend continuation.
- derived_from: `Volatility` - Momentum is interpreted relative to volatility.

### Liquidity
The ease with which the market can absorb orders without excessive price movement.

Aliases: None

Relationships:
- derived_from: `Session` - Liquidity often changes by session.
- supports: `Volatility` - Liquidity and volatility are linked market conditions.
- constrains: `Exposure` - Liquidity limits practical exposure.

### Volatility
The degree of price variation over time.

Aliases: None

Relationships:
- supports: `Market Regime` - Volatility helps classify regime.
- explains: `Trade Health` - Trade health depends on volatility context.

### Pattern
A repeatable market structure or configuration identified from price, volume, or context.

Aliases: None

Relationships:
- supports: `Trend` - Patterns help confirm or weaken trend.
- produces: `Signal` - Patterns can generate candidate signals.
- uses: `Evidence` - Patterns must be supported by evidence.

### Portfolio
The aggregate business view of capital, holdings, exposures, and performance for one account or strategy set.

Aliases: None

Relationships:
- part_of: `Position` - A portfolio contains positions.
- part_of: `Exposure` - A portfolio aggregates exposures.
- constrains: `Risk` - Portfolio risk overrides opportunity.
- uses: `Trade` - Portfolio state changes from trades.

### Session
A bounded market trading interval such as open, auction, regular, or close.

Aliases: None

Relationships:
- uses: `Instrument` - Sessions are defined per market or instrument venue.
- supports: `Market Regime` - Session context can shape the active regime.
- supports: `Liquidity` - Session boundaries often affect liquidity.

### Instrument
The tradable market object identified by symbol and venue.

Aliases: Security

Relationships:
- uses: `Order` - Orders target a single instrument.
- uses: `Trade` - Trades execute in a single instrument.
- uses: `Position` - Positions are held in a single instrument.

### Strategy
A reusable business rule set that turns evidence into candidates, decisions, or signals.

Aliases: None

Relationships:
- produces: `Signal` - Strategies can produce signals.
- produces: `Candidate` - Strategies can produce candidates.
- uses: `Evidence` - Strategies must work from evidence rather than prediction.

### Evidence
A piece of observed or derived information that can support, weaken, or invalidate a business judgment.

Aliases: None

Relationships:
- supports: `Confidence` - Evidence supports confidence.
- supports: `Probability` - Evidence informs probability estimates.
- supports: `Trade Health` - Trade health is computed from evidence.
- supports: `Risk` - Risk assessment is evidence-based.

### Trade Health
A score describing the quality of an existing or potential trade under the current evidence set.

Aliases: None

Relationships:
- explains: `Trade` - Trade health describes the trade context.
- measured_by: `Risk` - Trade health includes risk as a factor.
- derived_from: `Evidence` - Trade health is derived from evidence.

## Relationship Map

- Trade: derived_from:Order, part_of:Position, explains:Risk, uses:Evidence
- Position: part_of:Instrument, part_of:Portfolio, measured_by:Exposure
- Order: uses:Instrument, derived_from:Signal, constrains:Risk
- Signal: produces:Candidate, derived_from:Strategy, uses:Evidence
- Candidate: measured_by:Confidence, measured_by:Probability, uses:Evidence, constrains:Risk
- Confidence: consumes:Probability, derived_from:Evidence
- Probability: supports:Confidence, derived_from:Evidence
- Risk: measured_by:Exposure, explains:Trade Health, constrains:Portfolio
- Exposure: derived_from:Position, part_of:Portfolio, measured_by:Risk
- Market Regime: derived_from:Trend, derived_from:Momentum, derived_from:Volatility, uses:Session
- Trend: supports:Momentum, explains:Pattern
- Momentum: supports:Trend, derived_from:Volatility
- Liquidity: derived_from:Session, supports:Volatility, constrains:Exposure
- Volatility: supports:Market Regime, explains:Trade Health
- Pattern: supports:Trend, produces:Signal, uses:Evidence
- Portfolio: part_of:Position, part_of:Exposure, constrains:Risk, uses:Trade
- Session: uses:Instrument, supports:Market Regime, supports:Liquidity
- Instrument: uses:Order, uses:Trade, uses:Position
- Strategy: produces:Signal, produces:Candidate, uses:Evidence
- Evidence: supports:Confidence, supports:Probability, supports:Trade Health, supports:Risk
- Trade Health: explains:Trade, measured_by:Risk, derived_from:Evidence
