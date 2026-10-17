from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tranalyset.contracts.events import EventType
from tranalyset.contracts.payloads import require_text

KNOWLEDGE_RETRIEVED = EventType("KnowledgeRetrieved")


class KnowledgeSourceType(StrEnum):
    TRADING_BOOKS = "trading_books"
    RESEARCH_PAPERS = "research_papers"
    TRADING_JOURNAL = "trading_journal"
    HISTORICAL_TRADES = "historical_trades"
    COMPANY_DOCUMENTS = "company_documents"
    MARKET_CONCEPTS = "market_concepts"


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    document_id: str
    source_type: KnowledgeSourceType
    title: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.document_id, "document_id")
        require_text(self.title, "title")
        require_text(self.content, "content")


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    document_id: str
    source_type: KnowledgeSourceType
    title: str
    excerpt: str
    relevance_score: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievedPayload:
    retrieved_at: str
    query: str
    results: Sequence[KnowledgeSearchResult]

    def to_payload(self) -> dict[str, Any]:
        require_text(self.query, "query")
        return {
            "retrieved_at": self.retrieved_at,
            "query": self.query,
            "results": [
                {
                    "document_id": result.document_id,
                    "source_type": result.source_type.value,
                    "title": result.title,
                    "excerpt": result.excerpt,
                    "relevance_score": result.relevance_score,
                    "metadata": dict(result.metadata),
                }
                for result in self.results
            ],
        }


PUBLISHED_EVENTS = (KNOWLEDGE_RETRIEVED,)
