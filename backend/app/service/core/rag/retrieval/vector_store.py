from __future__ import annotations
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import logging
from service.core.rag.utils.es_conn import ESConnection
from service.core.rag.nlp.model import generate_embedding
import time


@dataclass
class RetrieveQuery:
    text: str
    kb_id: int
    top_k: int = 5
    focus_doc_ids: Optional[List[int]] = None
    index_override: Optional[str] = None  # for session-level index
    use_vector: bool = True  # enable hybrid retrieval (text + vector)


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    metadata: Dict[str, Any]


class VectorStore:
    def search(self, *, query: RetrieveQuery) -> List[RetrievedChunk]:
        raise NotImplementedError


class ESVectoreStore(VectorStore):
    def __init__(self, default_index: str | None = None) -> None:
        self.es = ESConnection()
        self.default_index = default_index
        self.logger = logging.getLogger("rag.retriever.es")

    def search(self, *, query: RetrieveQuery) -> List[RetrievedChunk]:
        index_name = query.index_override or self.default_index or "scholarmind_default"
        from service.core.rag.utils.doc_store_conn import MatchTextExpr, MatchDenseExpr, FusionExpr, OrderByExpr

        match_exprs = []
        # 1) text match
        match_exprs.append(
            MatchTextExpr(
                fields=["text"],
                matching_text=query.text,
                topn=max(query.top_k * 2, 10),
                extra_options={"minimum_should_match": 0.0},
            )
        )
        # 2) optional vector match (hybrid)
        if query.use_vector:
            try:
                q_emb = generate_embedding([query.text])
                if q_emb and q_emb[0] is not None:
                    match_exprs.append(
                        MatchDenseExpr(
                            vector_column_name="vector",
                            embedding_data=q_emb[0],
                            embedding_data_type="float32",
                            distance_type="cosine",
                            topn=max(query.top_k, 10),
                            extra_options={"similarity": 0.0},
                        )
                    )
                    # Fusion weights: text:vector = 0.5:0.5（可后续调参/从defaults读取）
                    match_exprs.append(
                        FusionExpr(method="weighted_sum", topn=query.top_k, fusion_params={"weights": "0.5,0.5"})
                    )
            except Exception as e:
                # 发生异常则退化为纯文本检索
                try:
                    self.logger.warning(f"Query embedding failed, fallback to text-only: {e}")
                except Exception:
                    pass

        # 3) optional filters
        condition: Dict[str, Any] = {}
        if query.focus_doc_ids:
            condition["document_id"] = [str(d) for d in query.focus_doc_ids if d is not None]

        # 4) execute
        t0 = time.time()
        from service.core.rag.utils.doc_store_conn import OrderByExpr as _OrderBy
        res = self.es.search(
            selectFields=["text", "kb_id", "document_id", "page", "offset_start", "offset_end"],
            highlightFields=["text"],
            condition=condition,
            matchExprs=match_exprs,
            orderBy=_OrderBy().desc("_score"),
            offset=0,
            limit=max(query.top_k * 2, 10),  # 拉宽召回，再做去重与排序
            indexNames=index_name,
            knowledgebaseIds=[str(query.kb_id)],
            aggFields=[],
            rank_feature=None,
        )
        took_ms = int((time.time() - t0) * 1000)

        hits = res.get("hits", {}).get("hits", [])
        # 5) transform -> RetrievedChunk
        raw_chunks: List[RetrievedChunk] = []
        for h in hits:
            src = h.get("_source", {})
            md = {
                "kb_id": src.get("kb_id"),
                "document_id": src.get("document_id"),
                "page": src.get("page"),
                "offset_start": src.get("offset_start"),
                "offset_end": src.get("offset_end"),
            }
            raw_chunks.append(
                RetrievedChunk(
                    chunk_id=h.get("_id", ""),
                    text=src.get("text", ""),
                    score=float(h.get("_score", 0.0) or 0.0),
                    metadata=md,
                )
            )

        # 6) de-dup by (document_id, page, offsets or normalized text)
        seen_keys: set[str] = set()
        deduped: List[RetrievedChunk] = []
        for c in raw_chunks:
            doc_id = str((c.metadata or {}).get("document_id", ""))
            page = str((c.metadata or {}).get("page", ""))
            off_s = str((c.metadata or {}).get("offset_start", ""))
            off_e = str((c.metadata or {}).get("offset_end", ""))
            key = f"{doc_id}-{page}-{off_s}-{off_e}"
            # 若缺 offset 信息，退化为文本首64字符做粗去重
            if key == "---":
                key = (doc_id + ":" + (c.text or "")[:64].strip().lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(c)

        # 7) sort by score desc, then page asc, offset asc
        def sort_key(c: RetrievedChunk):
            md = c.metadata or {}
            page = md.get("page") or 1
            off_s = md.get("offset_start") or 0
            return (-float(c.score or 0.0), int(page), int(off_s))

        deduped.sort(key=sort_key)
        final_chunks = deduped[: query.top_k]

        try:
            self.logger.info(
                f"ESRetriever: q='{query.text[:64]}' kb={query.kb_id} index={index_name} top_k={query.top_k} raw={len(raw_chunks)} final={len(final_chunks)} took_ms={took_ms}"
            )
        except Exception:
            pass
        return final_chunks
