from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tranalyset.contracts.events import EventEnvelope, EventType
from tranalyset.kernel.kernel import Microkernel
from tranalyset.kernel.loader import StaticPluginLoader
from tranalyset.plugins.ai_reasoning_engine import AIReasoningEnginePlugin
from tranalyset.plugins.ai_reasoning_engine.contracts import REASONING_GENERATED
from tranalyset.plugins.knowledge_engine import (
    KnowledgeDocument,
    KnowledgeEnginePlugin,
    KnowledgeSourceType,
)
from tranalyset.plugins.risk import RISK_APPROVED


def test_knowledge_engine_indexes_documents_and_supports_semantic_search() -> None:
    plugin = KnowledgeEnginePlugin()

    plugin.index_documents(
        (
            KnowledgeDocument(
                document_id="doc-1",
                source_type=KnowledgeSourceType.TRADING_BOOKS,
                title="Trend Following with Moving Averages",
                content="Trend following systems use moving averages to identify momentum and manage entries.",
                metadata={"author": "A. Trader"},
            ),
            KnowledgeDocument(
                document_id="doc-2",
                source_type=KnowledgeSourceType.RESEARCH_PAPERS,
                title="Portfolio Risk and Drawdown Control",
                content="Risk management requires drawdown control, position sizing, and stop loss discipline.",
                metadata={"year": 2023},
            ),
        )
    )

    results = plugin.search("moving averages and momentum", limit=3)

    assert results
    assert results[0].document_id == "doc-1"
    assert results[0].source_type is KnowledgeSourceType.TRADING_BOOKS
    assert results[0].relevance_score > 0.0
    assert "moving averages" in results[0].excerpt.lower()


async def test_knowledge_engine_retriever_integrates_with_ai_reasoning_engine() -> None:
    knowledge_engine = KnowledgeEnginePlugin()
    knowledge_engine.index_document(
        KnowledgeDocument(
            document_id="doc-3",
            source_type=KnowledgeSourceType.COMPANY_DOCUMENTS,
            title="Risk Policy",
            content="Risk policy requires position sizing and stop loss discipline for drawdown control.",
            metadata={"section": "risk"},
        )
    )
    reasoning_engine = AIReasoningEnginePlugin()
    kernel = Microkernel(plugin_loader=StaticPluginLoader([knowledge_engine, reasoning_engine]))
    published: list[EventEnvelope] = []

    async def collect(event: EventEnvelope) -> None:
        published.append(event)

    await kernel.event_bus.subscribe(REASONING_GENERATED, collect)
    await kernel.start()
    await kernel.event_bus.publish(
        EventEnvelope.create(
            source="risk",
            event_type=RISK_APPROVED,
            payload={
                "trade_id": "trade-1",
                "symbol": "AAPL",
                "reason": "risk within configured limit",
                "evidence": {"portfolio_risk": 0.02},
            },
        )
    )
    await kernel.event_bus.wait_until_idle()
    await kernel.stop()

    assert len(published) == 1
    payload = published[0].payload
    retrieved_context = payload["evidence"]["retrieved_context"]
    assert retrieved_context[0]["source_type"] == "company_documents"
    assert retrieved_context[0]["content"]["document_id"] == "doc-3"
