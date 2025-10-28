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
            selectFields=["text", "kb_id", "document_id", "page", "offset_start", "offset_end", 
                         "element_type", "prev_chunk_id", "next_chunk_id", "chunk_index"],
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
                "element_type": src.get("element_type"),
                "prev_chunk_id": src.get("prev_chunk_id"),
                "next_chunk_id": src.get("next_chunk_id"),
                "chunk_index": src.get("chunk_index"),
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
            md = c.metadata or {}
            doc_id = str(md.get("document_id") or "")
            page = str(md.get("page") or "")
            off_s = md.get("offset_start")
            off_e = md.get("offset_end")
            if off_s is None or off_e is None or str(off_s) == "" or str(off_e) == "":
                # 退化：使用 (doc_id,page,文本前缀) 作为 key，增强稳定性
                key = f"{doc_id}:{page}:{(c.text or '')[:64].strip().lower()}"
            else:
                key = f"{doc_id}-{page}-{off_s}-{off_e}"
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

        # 8) 公式块上下文扩展（Equation Context Expansion）
        from core.config import settings
        if getattr(settings, "SM_EQUATION_CONTEXT_EXPANSION", True):
            final_chunks = self._expand_equation_context(
                chunks=final_chunks,
                index_name=index_name,
                kb_id=query.kb_id,
            )

        try:
            self.logger.info(
                f"ESRetriever: q='{query.text[:64]}' kb={query.kb_id} index={index_name} top_k={query.top_k} raw={len(raw_chunks)} final={len(final_chunks)} took_ms={took_ms}"
            )
        except Exception:
            pass
        return final_chunks
    
    def _expand_equation_context(
        self,
        chunks: List[RetrievedChunk],
        index_name: str,
        kb_id: int,
    ) -> List[RetrievedChunk]:
        """
        为公式块自动扩展上下文（前后各1个文本块）
        
        Args:
            chunks: 原始检索结果
            index_name: 索引名称
            kb_id: 知识库 ID
            
        Returns:
            扩展后的 chunks 列表（保持原始顺序，公式块后附加上下文）
        """
        from core.config import settings
        
        prev_count = getattr(settings, "SM_EQUATION_EXPANSION_PREV", 1)
        next_count = getattr(settings, "SM_EQUATION_EXPANSION_NEXT", 1)
        
        expanded_chunks: List[RetrievedChunk] = []
        seen_chunk_ids: set[str] = set()
        
        for chunk in chunks:
            # 添加原始 chunk
            expanded_chunks.append(chunk)
            seen_chunk_ids.add(chunk.chunk_id)
            
            # 检查是否为公式块
            element_type = chunk.metadata.get("element_type", "")
            if element_type != "equation_latex":
                continue
            
            # 获取相邻块 ID
            prev_id = chunk.metadata.get("prev_chunk_id")
            next_id = chunk.metadata.get("next_chunk_id")
            
            # 收集需要获取的相邻块 ID
            context_ids = []
            if prev_id and prev_count > 0:
                context_ids.append(prev_id)
            if next_id and next_count > 0:
                context_ids.append(next_id)
            
            if not context_ids:
                continue
            
            # 批量获取相邻块
            try:
                context_chunks = self._fetch_chunks_by_ids(
                    chunk_ids=context_ids,
                    index_name=index_name,
                    kb_id=kb_id,
                )
                
                # 按照 prev -> next 的顺序添加上下文
                for ctx_chunk in context_chunks:
                    if ctx_chunk.chunk_id in seen_chunk_ids:
                        continue
                    seen_chunk_ids.add(ctx_chunk.chunk_id)
                    # 标记为上下文块（降低 score，避免干扰排序）
                    ctx_chunk.score = chunk.score * 0.5
                    ctx_chunk.metadata["is_context"] = True
                    ctx_chunk.metadata["context_for_chunk_id"] = chunk.chunk_id
                    expanded_chunks.append(ctx_chunk)
                
                try:
                    self.logger.info(
                        f"EquationContextExpansion: equation_chunk={chunk.chunk_id} added_context={len(context_chunks)}"
                    )
                except Exception:
                    pass
                    
            except Exception as e:
                try:
                    self.logger.warning(f"Failed to expand context for equation chunk {chunk.chunk_id}: {e}")
                except Exception:
                    pass
        
        return expanded_chunks
    
    def _fetch_chunks_by_ids(
        self,
        chunk_ids: List[str],
        index_name: str,
        kb_id: int,
    ) -> List[RetrievedChunk]:
        """
        根据 chunk ID 批量获取 chunks
        
        Args:
            chunk_ids: chunk ID 列表
            index_name: 索引名称
            kb_id: 知识库 ID
            
        Returns:
            RetrievedChunk 列表
        """
        if not chunk_ids:
            return []
        
        try:
            # 使用 Elasticsearch 的 mget (multi-get) API
            body = {"ids": chunk_ids}
            response = self.es.conn.mget(index=index_name, body=body)
            
            chunks: List[RetrievedChunk] = []
            for doc in response.get("docs", []):
                if not doc.get("found"):
                    continue
                
                src = doc.get("_source", {})
                # 验证 kb_id 匹配
                if str(src.get("kb_id")) != str(kb_id):
                    continue
                
                md = {
                    "kb_id": src.get("kb_id"),
                    "document_id": src.get("document_id"),
                    "page": src.get("page"),
                    "offset_start": src.get("offset_start"),
                    "offset_end": src.get("offset_end"),
                    "element_type": src.get("element_type"),
                    "prev_chunk_id": src.get("prev_chunk_id"),
                    "next_chunk_id": src.get("next_chunk_id"),
                    "chunk_index": src.get("chunk_index"),
                }
                
                chunks.append(
                    RetrievedChunk(
                        chunk_id=doc.get("_id", ""),
                        text=src.get("text", ""),
                        score=0.0,  # 上下文块的 score 将在调用方设置
                        metadata=md,
                    )
                )
            
            return chunks
            
        except Exception as e:
            try:
                self.logger.error(f"Failed to fetch chunks by IDs: {e}")
            except Exception:
                pass
            return []
