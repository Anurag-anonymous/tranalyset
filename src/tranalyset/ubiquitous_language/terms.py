from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class RelationshipType(StrEnum):
    IS_A = "is_a"
    PART_OF = "part_of"
    USES = "uses"
    PRODUCES = "produces"
    CONSUMES = "consumes"
    DERIVED_FROM = "derived_from"
    MEASURED_BY = "measured_by"
    CONSTRAINS = "constrains"
    EXPLAINS = "explains"
    SUPPORTS = "supports"


RELATIONSHIP_TYPES = tuple(RelationshipType)


@dataclass(frozen=True, slots=True)
class Relationship:
    target: str
    relationship_type: RelationshipType
    description: str

    def __post_init__(self) -> None:
        if not self.target.strip():
            msg = "Relationship target must not be empty."
            raise ValueError(msg)
        if not self.description.strip():
            msg = "Relationship description must not be empty."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Term:
    name: str
    definition: str
    relationships: tuple[Relationship, ...] = ()
    aliases: tuple[str, ...] = ()
    notes: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_name = self.name.strip()
        if not normalized_name:
            msg = "Term name must not be empty."
            raise ValueError(msg)
        normalized_definition = self.definition.strip()
        if not normalized_definition:
            msg = "Term definition must not be empty."
            raise ValueError(msg)
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "definition", normalized_definition)
        object.__setattr__(self, "relationships", tuple(self.relationships))
        object.__setattr__(self, "aliases", tuple(alias.strip() for alias in self.aliases if alias.strip()))
        if self.notes is not None and not self.notes.strip():
            msg = "Term notes must be non-empty if provided."
            raise ValueError(msg)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def relationship_targets(self) -> tuple[str, ...]:
        return tuple(relationship.target for relationship in self.relationships)

    def to_markdown(self) -> str:
        aliases = ", ".join(self.aliases) if self.aliases else "None"
        relationships = (
            "\n".join(
                f"- {relationship.relationship_type.value}: `{relationship.target}` - {relationship.description}"
                for relationship in self.relationships
            )
            if self.relationships
            else "- None"
        )
        notes = f"\n\nNotes: {self.notes}" if self.notes else ""
        return (
            f"### {self.name}\n"
            f"{self.definition}\n\n"
            f"Aliases: {aliases}\n\n"
            f"Relationships:\n{relationships}{notes}"
        )

    def __hash__(self) -> int:
        return hash((self.name, self.definition, self.relationships, self.aliases, self.notes, tuple(sorted(self.metadata.items()))))


TERM_DEFINITIONS: tuple[Term, ...] = (
    Term(
        name="Trade",
        definition="An executed exchange of an instrument that changes cash and position state.",
        aliases=("Execution",),
        relationships=(
            Relationship("Order", RelationshipType.DERIVED_FROM, "A trade is produced from a filled order."),
            Relationship("Position", RelationshipType.PART_OF, "Trades open, reduce, or close positions."),
            Relationship("Risk", RelationshipType.EXPLAINS, "Risk evidence governs whether a trade may proceed."),
            Relationship("Evidence", RelationshipType.USES, "Trade history is evidence for risk, learning, and reasoning."),
        ),
    ),
    Term(
        name="Position",
        definition="The signed quantity of a specific instrument held by a portfolio or account.",
        aliases=("Holding",),
        relationships=(
            Relationship("Instrument", RelationshipType.PART_OF, "A position always refers to a single instrument."),
            Relationship("Portfolio", RelationshipType.PART_OF, "Positions are held inside portfolios."),
            Relationship("Exposure", RelationshipType.MEASURED_BY, "Positions create directional exposure."),
        ),
    ),
    Term(
        name="Order",
        definition="An instruction to buy or sell an instrument under defined execution rules.",
        relationships=(
            Relationship("Instrument", RelationshipType.USES, "Every order targets one instrument."),
            Relationship("Signal", RelationshipType.DERIVED_FROM, "Orders may be generated from a signal or manual intent."),
            Relationship("Risk", RelationshipType.CONSTRAINS, "Risk rules validate whether an order can proceed."),
        ),
    ),
    Term(
        name="Signal",
        definition="A structured directional or state-oriented message that expresses trading intent, not execution.",
        relationships=(
            Relationship("Candidate", RelationshipType.PRODUCES, "Signals can emerge from candidate evaluation."),
            Relationship("Strategy", RelationshipType.DERIVED_FROM, "Signals are often produced by strategy logic."),
            Relationship("Evidence", RelationshipType.USES, "Signals should be backed by evidence."),
        ),
    ),
    Term(
        name="Candidate",
        definition="A provisional opportunity or state change that may later become a signal, order, or decision.",
        relationships=(
            Relationship("Confidence", RelationshipType.MEASURED_BY, "Candidates carry confidence values."),
            Relationship("Probability", RelationshipType.MEASURED_BY, "Candidates express likelihood estimates."),
            Relationship("Evidence", RelationshipType.USES, "Candidates are justified by evidence."),
            Relationship("Risk", RelationshipType.CONSTRAINS, "Risk determines whether a candidate can advance."),
        ),
    ),
    Term(
        name="Confidence",
        definition="The system's bounded degree of trust in a statement, score, or inference.",
        aliases=("ConfidenceScore",),
        relationships=(
            Relationship("Probability", RelationshipType.CONSUMES, "Confidence often wraps probability-like evidence."),
            Relationship("Evidence", RelationshipType.DERIVED_FROM, "Confidence should be derived from supporting evidence."),
        ),
    ),
    Term(
        name="Probability",
        definition="A bounded likelihood that an event or hypothesis will occur or hold true.",
        relationships=(
            Relationship("Confidence", RelationshipType.SUPPORTS, "Probability is a common input to confidence."),
            Relationship("Evidence", RelationshipType.DERIVED_FROM, "Probability estimates are derived from evidence."),
        ),
    ),
    Term(
        name="Risk",
        definition="The possibility of adverse outcome, measured and constrained before opportunity is accepted.",
        relationships=(
            Relationship("Exposure", RelationshipType.MEASURED_BY, "Risk is shaped by exposure."),
            Relationship("Trade Health", RelationshipType.EXPLAINS, "Trade health reflects risk quality."),
            Relationship("Portfolio", RelationshipType.CONSTRAINS, "Portfolio-level risk overrides local opportunity."),
        ),
    ),
    Term(
        name="Exposure",
        definition="Capital-at-risk or directional market participation attributed to an instrument, sector, or portfolio.",
        relationships=(
            Relationship("Position", RelationshipType.DERIVED_FROM, "Exposure is derived from positions."),
            Relationship("Portfolio", RelationshipType.PART_OF, "Exposure is aggregated at the portfolio level."),
            Relationship("Risk", RelationshipType.MEASURED_BY, "Risk control is often expressed through exposure limits."),
        ),
    ),
    Term(
        name="Market Regime",
        definition="The current market condition class used to summarize the active environment for decision-making.",
        aliases=("Regime",),
        relationships=(
            Relationship("Trend", RelationshipType.DERIVED_FROM, "Trend is a primary input to regime classification."),
            Relationship("Momentum", RelationshipType.DERIVED_FROM, "Momentum helps identify the active regime."),
            Relationship("Volatility", RelationshipType.DERIVED_FROM, "Volatility is a regime-defining feature."),
            Relationship("Session", RelationshipType.USES, "Session context can alter regime interpretation."),
        ),
    ),
    Term(
        name="Trend",
        definition="The directional bias of price over time.",
        relationships=(
            Relationship("Momentum", RelationshipType.SUPPORTS, "Momentum helps confirm trend strength."),
            Relationship("Pattern", RelationshipType.EXPLAINS, "Patterns often form around trend structure."),
        ),
    ),
    Term(
        name="Momentum",
        definition="The persistence or acceleration of price movement over a measured period.",
        relationships=(
            Relationship("Trend", RelationshipType.SUPPORTS, "Momentum helps validate trend continuation."),
            Relationship("Volatility", RelationshipType.DERIVED_FROM, "Momentum is interpreted relative to volatility."),
        ),
    ),
    Term(
        name="Liquidity",
        definition="The ease with which the market can absorb orders without excessive price movement.",
        relationships=(
            Relationship("Session", RelationshipType.DERIVED_FROM, "Liquidity often changes by session."),
            Relationship("Volatility", RelationshipType.SUPPORTS, "Liquidity and volatility are linked market conditions."),
            Relationship("Exposure", RelationshipType.CONSTRAINS, "Liquidity limits practical exposure."),
        ),
    ),
    Term(
        name="Volatility",
        definition="The degree of price variation over time.",
        relationships=(
            Relationship("Market Regime", RelationshipType.SUPPORTS, "Volatility helps classify regime."),
            Relationship("Trade Health", RelationshipType.EXPLAINS, "Trade health depends on volatility context."),
        ),
    ),
    Term(
        name="Pattern",
        definition="A repeatable market structure or configuration identified from price, volume, or context.",
        relationships=(
            Relationship("Trend", RelationshipType.SUPPORTS, "Patterns help confirm or weaken trend."),
            Relationship("Signal", RelationshipType.PRODUCES, "Patterns can generate candidate signals."),
            Relationship("Evidence", RelationshipType.USES, "Patterns must be supported by evidence."),
        ),
    ),
    Term(
        name="Portfolio",
        definition="The aggregate business view of capital, holdings, exposures, and performance for one account or strategy set.",
        relationships=(
            Relationship("Position", RelationshipType.PART_OF, "A portfolio contains positions."),
            Relationship("Exposure", RelationshipType.PART_OF, "A portfolio aggregates exposures."),
            Relationship("Risk", RelationshipType.CONSTRAINS, "Portfolio risk overrides opportunity."),
            Relationship("Trade", RelationshipType.USES, "Portfolio state changes from trades."),
        ),
    ),
    Term(
        name="Session",
        definition="A bounded market trading interval such as open, auction, regular, or close.",
        relationships=(
            Relationship("Instrument", RelationshipType.USES, "Sessions are defined per market or instrument venue."),
            Relationship("Market Regime", RelationshipType.SUPPORTS, "Session context can shape the active regime."),
            Relationship("Liquidity", RelationshipType.SUPPORTS, "Session boundaries often affect liquidity."),
        ),
    ),
    Term(
        name="Instrument",
        definition="The tradable market object identified by symbol and venue.",
        aliases=("Security",),
        relationships=(
            Relationship("Order", RelationshipType.USES, "Orders target a single instrument."),
            Relationship("Trade", RelationshipType.USES, "Trades execute in a single instrument."),
            Relationship("Position", RelationshipType.USES, "Positions are held in a single instrument."),
        ),
    ),
    Term(
        name="Strategy",
        definition="A reusable business rule set that turns evidence into candidates, decisions, or signals.",
        relationships=(
            Relationship("Signal", RelationshipType.PRODUCES, "Strategies can produce signals."),
            Relationship("Candidate", RelationshipType.PRODUCES, "Strategies can produce candidates."),
            Relationship("Evidence", RelationshipType.USES, "Strategies must work from evidence rather than prediction."),
        ),
    ),
    Term(
        name="Evidence",
        definition="A piece of observed or derived information that can support, weaken, or invalidate a business judgment.",
        relationships=(
            Relationship("Confidence", RelationshipType.SUPPORTS, "Evidence supports confidence."),
            Relationship("Probability", RelationshipType.SUPPORTS, "Evidence informs probability estimates."),
            Relationship("Trade Health", RelationshipType.SUPPORTS, "Trade health is computed from evidence."),
            Relationship("Risk", RelationshipType.SUPPORTS, "Risk assessment is evidence-based."),
        ),
    ),
    Term(
        name="Trade Health",
        definition="A score describing the quality of an existing or potential trade under the current evidence set.",
        relationships=(
            Relationship("Trade", RelationshipType.EXPLAINS, "Trade health describes the trade context."),
            Relationship("Risk", RelationshipType.MEASURED_BY, "Trade health includes risk as a factor."),
            Relationship("Evidence", RelationshipType.DERIVED_FROM, "Trade health is derived from evidence."),
        ),
    ),
)

TERMS: Mapping[str, Term] = MappingProxyType({term.name: term for term in TERM_DEFINITIONS})
CANONICAL_TERM_NAMES = tuple(TERMS.keys())


def get_term(name: str) -> Term:
    normalized = name.strip()
    if normalized not in TERMS:
        msg = f"Unknown ubiquitous language term: {name}"
        raise KeyError(msg)
    return TERMS[normalized]
