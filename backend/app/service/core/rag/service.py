from __future__ import annotations
from typing import List, Dict, Any, Generator, Optional
from dataclasses import dataclass
import re
from service.core.rag.retrieval.vector_store import ESVectoreStore, RetrieveQuery
from service.core.rag.prompt.builder import PromptBuilder
from service.core.rag.llm.client import LLMClient
from core.config import settings
import logging
import time


@dataclass
class RAGResult:
    chunks: List[Dict[str, Any]]
    answer: str


class RAGService:
    def __init__(self) -> None:
        self.store = ESVectoreStore(default_index=settings.ES_DEFAULT_INDEX)
        self.prompt = PromptBuilder(
            language=settings.SM_DEFAULT_LANGUAGE,
            enable_citations=settings.SM_ENABLE_CITATIONS,
            max_context_chars=6000,
        )
        self.llm = LLMClient()
        self.logger = logging.getLogger("rag.service")
        self._last_usage: Dict[str, Any] | None = None

    def retrieve(
        self,
        *,
        query: str,
        kb_id: int,
        top_k: int = 5,
        focus_doc_ids: Optional[List[int]] = None,
        use_vector: bool = True,
        index_override: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rq = RetrieveQuery(
            text=query,
            kb_id=kb_id,
            top_k=top_k,
            focus_doc_ids=focus_doc_ids,
            index_override=index_override,
            use_vector=use_vector,
        )
        t0 = time.time()
        results = self.store.search(query=rq)
        dt = int((time.time() - t0) * 1000)
        try:
            self.logger.info(
                f"RAG.retrieve kb={kb_id} top_k={top_k} focus={len(focus_doc_ids or [])} hits={len(results)} took_ms={dt} index={'session' if index_override else 'default'}"
            )
        except Exception:
            pass
        # convert to common dict format for prompt
        chunks: List[Dict[str, Any]] = []
        for r in results:
            chunks.append({"text": r.text, "metadata": r.metadata, "score": r.score, "chunk_id": r.chunk_id})
        return chunks

    def generate(self, *, question: str, chunks: List[Dict[str, Any]], temperature: float = None, max_tokens: int = None, stream: bool = True):
        t0 = time.time()
        sections = self.prompt.build(question=question, chunks=chunks)
        messages = [{"role": s.role, "content": s.content} for s in sections]
        temperature = settings.SM_TEMPERATURE if temperature is None else temperature
        max_tokens = settings.SM_MAX_TOKENS if max_tokens is None else max_tokens
        try:
            self.logger.info(f"RAG.generate stream={stream} temp={temperature} max_tokens={max_tokens} prompt_chars={sum(len(m['content']) for m in messages)}")
        except Exception:
            pass
        out = self.llm.generate(messages, temperature=temperature, max_tokens=max_tokens, stream=stream)
        if not stream:
            try:
                self.logger.info(f"RAG.generate done took_ms={int((time.time()-t0)*1000)}")
            except Exception:
                pass
            prompt_chars = sum(len(m["content"]) for m in messages)
            completion_chars = len(out or "")
            ratio = 4 if self.prompt.language == "en" else 1
            self._last_usage = {
                "prompt_tokens": prompt_chars // ratio,
                "completion_tokens": completion_chars // ratio,
                "total_tokens": (prompt_chars + completion_chars) // ratio,
            }
            # 将模型正文中的 [doc_id:page] 等变体规范为 [id:page]
            try:
                out = self._normalize_citations(out)
            except Exception:
                pass
        return out

    def get_last_usage(self) -> Dict[str, Any] | None:
        return self._last_usage

    # --- helpers ---
    def _normalize_citations(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        # 规范三类形式：
        # [doc_id:82:1] 或 [document_id:82:1] 或 [82:1] → [82:1]
        # 允许中英文提示词混入
        # 先把 [doc_id:82:1]、[document_id:82:1]、[文档ID:82:1] 等替换成 [82:1]
        patterns = [r"\[(?:doc(?:ument)?_?id|documentId|文档ID)\s*:\s*(\d+)\s*:\s*(\d+)\]",
                    r"\[(\d+)\s*:\s*(\d+)\]"]
        def repl(m: re.Match) -> str:
            return f"[{m.group(1)}:{m.group(2)}]"
        # 逐个替换复杂前缀形式
        text = re.sub(patterns[0], repl, text, flags=re.IGNORECASE)
        # 第二个是标准形式，保持不变（这里是幂等处理）
        return text

    def ask_stream(
        self,
        *,
        question: str,
        kb_id: int,
        top_k: int = 5,
        focus_doc_ids: Optional[List[int]] = None,
        index_override: Optional[str] = None,
    ) -> Generator[str, None, None]:
        chunks = self.retrieve(
            query=question,
            kb_id=kb_id,
            top_k=top_k,
            focus_doc_ids=focus_doc_ids,
            index_override=index_override,
        )
        for part in self.generate(question=question, chunks=chunks, stream=True):
            yield part

    # --- citations helper ---
    def build_citations(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Aggregate chunks into citation objects.
        Structure: {document_id, page, chunk_id, score, snippet, offsets}
        """
        citations: List[Dict[str, Any]] = []
        for c in chunks:
            md = c.get("metadata", {}) or {}
            text = (c.get("text") or c.get("content") or "").strip()
            citations.append({
                "document_id": md.get("document_id"),
                "page": md.get("page"),
                "chunk_id": c.get("chunk_id"),
                "score": c.get("score"),
                "snippet": text[:300],
                "offsets": {
                    "start": md.get("offset_start", 0),
                    "end": md.get("offset_end", 0),
                },
            })
        return citations
