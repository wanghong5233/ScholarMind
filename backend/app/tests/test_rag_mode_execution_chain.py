from __future__ import annotations

from typing import Any, Dict, List

from core.config import settings
from service.core.rag.retrieval.vector_store import RetrievedChunk
from service.core.rag.service import RAGService


class _DummyStore:
    default_index = "scholarmind_default"

    def index_exists(self, index_name: str) -> bool:
        return bool(index_name)

    def search_multi_path(
        self,
        *,
        query,
        channels: List[str] | None = None,
        candidate_multiplier: int | None = None,
    ) -> List[RetrievedChunk]:
        metadata_base: Dict[str, Any] = {
            "document_id": 1,
            "page": 1,
            "index_name": query.index_override or self.default_index,
        }
        tag = query.query_tag or "q"
        return [
            RetrievedChunk(
                chunk_id=f"{tag}-1",
                text=f"{query.text} primary",
                score=0.95,
                metadata=dict(metadata_base),
                source="bm25",
                rank=1,
            ),
            RetrievedChunk(
                chunk_id=f"{tag}-2",
                text=f"{query.text} secondary",
                score=0.82,
                metadata=dict(metadata_base),
                source="bm25",
                rank=2,
            ),
        ]


def test_retrieve_routes_fast_and_deep(monkeypatch) -> None:
    service = RAGService()
    calls: List[str] = []

    def _fast(**kwargs):
        calls.append("fast")
        return []

    def _deep(**kwargs):
        calls.append("deep")
        return []

    monkeypatch.setattr(service, "_retrieve_fast_path", _fast)
    monkeypatch.setattr(service, "_retrieve_multi_stage", _deep)

    service.retrieve(query="test", kb_id=1, provider="multi_stage")
    service.retrieve(query="test", kb_id=1, provider="multimodal_graph")

    assert calls == ["fast", "deep"]


def test_fast_path_debug_and_variant_cap(monkeypatch) -> None:
    service = RAGService()
    service.store = _DummyStore()

    monkeypatch.setattr(settings, "SM_FAST_MODE_RECALL_SOURCES", "bm25")
    monkeypatch.setattr(settings, "SM_FAST_MODE_MAX_VARIANTS", 1)
    monkeypatch.setattr(settings, "SM_FAST_MODE_RECALL_MULTIPLIER", 1)
    monkeypatch.setattr(settings, "SM_FAST_MODE_CHANNEL_TOPK", 4)
    monkeypatch.setattr(settings, "SM_FAST_MODE_RERANK_ENABLED", False)

    monkeypatch.setattr(
        service,
        "_generate_query_variants",
        lambda *args, **kwargs: [
            {"text": "primary query", "tag": "original", "synthetic": False, "language": "en"},
            {"text": "rewrite query", "tag": "mq_1", "synthetic": False, "language": "en"},
            {"text": "hyde query", "tag": "hyde", "synthetic": True, "language": "en"},
        ],
    )

    payloads = service._retrieve_fast_path(
        query="primary query",
        kb_id=1,
        top_k=2,
        focus_doc_ids=None,
        boost_doc_ids=None,
        session_index=None,
        index_mode="auto",
        provider="multi_stage",
        extra_variants=None,
    )

    debug = service.get_last_retrieval_debug() or {}
    assert payloads
    assert debug.get("execution_chain") == "fast"
    assert debug.get("strategy") == "fast_path"
    assert len(debug.get("variants") or []) == 1
    assert all((item.get("metadata") or {}).get("fast_mode") for item in payloads)


def test_deep_path_debug_marked_as_deep(monkeypatch) -> None:
    service = RAGService()
    service.store = _DummyStore()

    monkeypatch.setattr(settings, "SM_EQUATION_CONTEXT_EXPANSION", False)
    monkeypatch.setattr(
        service,
        "_generate_query_variants",
        lambda *args, **kwargs: [
            {"text": "deep query", "tag": "original", "synthetic": False, "language": "en"},
        ],
    )

    payloads = service._retrieve_multi_stage(
        query="deep query",
        kb_id=1,
        top_k=2,
        focus_doc_ids=None,
        boost_doc_ids=None,
        boost_chunk_ids=None,
        session_index=None,
        index_mode="auto",
        provider="multimodal_graph",
        extra_variants=None,
    )

    debug = service.get_last_retrieval_debug() or {}
    assert payloads
    assert debug.get("execution_chain") == "deep"
    assert debug.get("strategy") == "deep_multi_stage"


def test_hyde_fallback_generates_variant(monkeypatch) -> None:
    service = RAGService()
    monkeypatch.setattr(settings, "SM_HYDE_FALLBACK_ENABLED", True)
    monkeypatch.setattr(service, "_generate_hyde_document", lambda *args, **kwargs: None)

    variants = service._generate_query_variants(
        "Graph neural networks for edge traffic scheduling",
        enable_translation=False,
        mq_num_override=1,
        enable_hyde=True,
        mode="deep",
    )

    assert any(item.get("tag") == "hyde" for item in variants)
    meta = service._last_variant_meta or {}
    assert meta.get("hyde_enabled") is True
    assert meta.get("hyde_generated") is True
    assert meta.get("hyde_fallback_used") is True
