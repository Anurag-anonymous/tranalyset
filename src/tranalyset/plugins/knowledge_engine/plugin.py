from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.contracts.plugins import HealthStatus, PluginHealth, PluginInstallContext, PluginMetadata
from tranalyset.plugin_sdk import BasePlugin, subscribes_to
from tranalyset.plugins.ai_reasoning_engine.reasoning import EvidenceReference, EvidenceRetriever
from tranalyset.plugins.knowledge_engine.contracts import (
    KNOWLEDGE_RETRIEVED,
    PUBLISHED_EVENTS,
    KnowledgeDocument,
    KnowledgeRetrievedPayload,
    KnowledgeSearchResult,
    KnowledgeSourceType,
)
from tranalyset.plugins.risk.contracts import RISK_APPROVED


class KnowledgeEnginePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(
            metadata=PluginMetadata(
                plugin_id="knowledge_engine",
                name="Knowledge Engine",
                version="1.0.0",
            )
        )
        self._documents: list[KnowledgeDocument] = []
        self._retrieval_count = 0

    async def on_install(self, context: PluginInstallContext) -> None:
        context.register_service(KnowledgeEnginePlugin, self)
        context.register_service(EvidenceRetriever, self)
        for event_type in PUBLISHED_EVENTS:
            context.register_event(event_type, description=f"Knowledge engine publishes {event_type}.")

    @subscribes_to(RISK_APPROVED)
    async def handle_risk_approved(self, event: EventEnvelope) -> None:
        query = _query_from_event(event)
        if not query:
            return
        results = self.search(query, limit=3)
        if not results:
            return
        await self.publish_knowledge_retrieved(query=query, results=results)

    async def publish_knowledge_retrieved(
        self,
        *,
        query: str,
        results: Sequence[KnowledgeSearchResult],
    ) -> None:
        payload = KnowledgeRetrievedPayload(
            retrieved_at=datetime.now(UTC).isoformat(),
            query=query,
            results=results,
        ).to_payload()
        await self.publish(KNOWLEDGE_RETRIEVED, payload=payload)

    def index_document(self, document: KnowledgeDocument) -> None:
        self._documents.append(document)

    def index_documents(self, documents: Sequence[KnowledgeDocument]) -> None:
        for document in documents:
            self.index_document(document)

    def search(self, query: str, *, limit: int = 5) -> tuple[KnowledgeSearchResult, ...]:
        normalized_query = " ".join(query.lower().split())
        if not normalized_query:
            return ()

        scored: list[tuple[float, KnowledgeSearchResult]] = []
        for document in self._documents:
            score = self._score(document, normalized_query)
            if score <= 0.0:
                continue
            scored.append(
                (
                    score,
                    KnowledgeSearchResult(
                        document_id=document.document_id,
                        source_type=document.source_type,
                        title=document.title,
                        excerpt=self._excerpt(document, normalized_query),
                        relevance_score=round(score, 4),
                        metadata=document.metadata,
                    ),
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        return tuple(result for _, result in scored[:limit])

    async def retrieve(self, event: EventEnvelope) -> Sequence[EvidenceReference]:
        query = _query_from_event(event)
        if not query:
            return ()
        results = self.search(query, limit=3)
        self._retrieval_count += 1
        return tuple(
            EvidenceReference(
                reference_id=result.document_id,
                source=result.source_type.value,
                content={
                    "document_id": result.document_id,
                    "source_type": result.source_type.value,
                    "title": result.title,
                    "excerpt": result.excerpt,
                    "relevance_score": result.relevance_score,
                },
            )
            for result in results
        )

    async def health_check(self) -> PluginHealth:
        base_health = await super().health_check()
        if base_health.status is not HealthStatus.HEALTHY:
            return base_health
        if not self._documents:
            return PluginHealth(
                status=HealthStatus.DEGRADED,
                message="Knowledge engine has no indexed documents yet.",
                details={"document_count": 0},
            )
        return PluginHealth(
            status=HealthStatus.HEALTHY,
            message="Knowledge engine is serving contextual knowledge.",
            details={"document_count": len(self._documents), "retrieval_count": self._retrieval_count},
        )

    def _score(self, document: KnowledgeDocument, normalized_query: str) -> float:
        tokens = set(normalized_query.split())
        content = document.content.lower()
        title = document.title.lower()
        score = 0.0
        for token in tokens:
            if token in content:
                score += 1.0
            if token in title:
                score += 1.5
        if not score:
            return 0.0
        if document.source_type.value in normalized_query:
            score += 0.5
        return score / max(len(tokens), 1)

    def _excerpt(self, document: KnowledgeDocument, normalized_query: str) -> str:
        if normalized_query in document.content.lower():
            start = document.content.lower().find(normalized_query)
            end = min(len(document.content), start + 120)
            return document.content[start:end].strip()
        return document.content[:120].strip()


def _query_from_event(event: EventEnvelope) -> str:
    payload = event.payload
    if isinstance(payload.get("reason"), str) and payload["reason"]:
        return str(payload["reason"])
    if isinstance(payload.get("evidence"), Mapping):
        evidence = payload["evidence"]
        if isinstance(evidence.get("portfolio_risk"), (int, float)):
            return "risk management and drawdown control"
    return ""


def create_plugin() -> KnowledgeEnginePlugin:
    return KnowledgeEnginePlugin()
