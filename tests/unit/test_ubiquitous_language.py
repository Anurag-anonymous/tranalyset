from __future__ import annotations

from pathlib import Path

from tranalyset.ubiquitous_language import CANONICAL_TERM_NAMES, TERMS, get_term, render_glossary_markdown
from tranalyset.ubiquitous_language.terms import RelationshipType


def test_canonical_terms_cover_the_system_vocabulary() -> None:
    expected = {
        "Trade",
        "Position",
        "Order",
        "Signal",
        "Candidate",
        "Confidence",
        "Probability",
        "Risk",
        "Exposure",
        "Market Regime",
        "Trend",
        "Momentum",
        "Liquidity",
        "Volatility",
        "Pattern",
        "Portfolio",
        "Session",
        "Instrument",
        "Strategy",
        "Evidence",
        "Trade Health",
    }

    assert set(CANONICAL_TERM_NAMES) == expected
    assert tuple(TERMS.keys()) == CANONICAL_TERM_NAMES


def test_terms_are_immutable_and_relationships_reference_known_terms() -> None:
    known = set(CANONICAL_TERM_NAMES)

    for name in CANONICAL_TERM_NAMES:
        term = get_term(name)
        assert term.name == name
        assert term.definition
        assert hash(term)
        for relationship in term.relationships:
            assert relationship.target in known
            assert relationship.relationship_type in RelationshipType


def test_glossary_document_matches_canonical_renderer() -> None:
    glossary_path = Path("docs/ubiquitous-language.md")
    rendered = render_glossary_markdown()

    assert glossary_path.exists()
    assert glossary_path.read_text(encoding="utf-8") == rendered
    for name in CANONICAL_TERM_NAMES:
        assert f"### {name}" in rendered


def test_aliases_remain_documented_where_appropriate() -> None:
    assert "Execution" in get_term("Trade").aliases
    assert "Holding" in get_term("Position").aliases
    assert "ConfidenceScore" in get_term("Confidence").aliases
