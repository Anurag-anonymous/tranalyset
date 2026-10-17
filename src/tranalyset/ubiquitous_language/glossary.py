from __future__ import annotations

from pathlib import Path

from tranalyset.ubiquitous_language.terms import CANONICAL_TERM_NAMES, TERMS


def render_glossary_markdown() -> str:
    lines: list[str] = []
    lines.append("# Ubiquitous Language")
    lines.append("")
    lines.append("This glossary defines the canonical business language used across Tranalyset.")
    lines.append("Each term has one authoritative definition in code and in documentation.")
    lines.append("")
    lines.append("## Canonical Terms")
    lines.append("")
    for name in CANONICAL_TERM_NAMES:
        term = TERMS[name]
        lines.append(term.to_markdown())
        lines.append("")
    lines.append("## Relationship Map")
    lines.append("")
    for name in CANONICAL_TERM_NAMES:
        term = TERMS[name]
        relationships = ", ".join(
            f"{relationship.relationship_type.value}:{relationship.target}"
            for relationship in term.relationships
        )
        lines.append(f"- {term.name}: {relationships or 'none'}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_glossary_file(path: str | Path) -> Path:
    destination = Path(path)
    destination.write_text(render_glossary_markdown(), encoding="utf-8")
    return destination
