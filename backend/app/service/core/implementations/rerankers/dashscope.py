from typing import List
import threading
import time
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank
from llama_index.core.schema import Node, NodeWithScore
from schemas.rag import Chunk
from service.core.abstractions.reranker import BaseReranker
from core.config import settings
from utils.get_logger import log

class DashScopeReranker(BaseReranker):
    """
    使用阿里云通义千问 (DashScope) Rerank API 进行重排序的实现类。
    """
    def __init__(self):
        if not settings.DASHSCOPE_API_KEY:
            raise ValueError("DASHSCOPE_API_KEY is not set in the environment.")

        self.model_name = str(getattr(settings, "SM_DASHSCOPE_RERANK_MODEL", "qwen3-rerank") or "qwen3-rerank")
        # DashScopeRerank 库在初始化时并不需要 top_n，top_n 在调用时指定。
        # 老版本依赖可能不支持 model 参数，做兼容回退。
        try:
            self.reranker = DashScopeRerank(api_key=settings.DASHSCOPE_API_KEY, model=self.model_name)
        except TypeError:
            self.reranker = DashScopeRerank(api_key=settings.DASHSCOPE_API_KEY)
            log.warning("DashScopeRerank does not support model param, fallback to library default model.")
            self.model_name = "library_default"
        self.fail_threshold = int(getattr(settings, "SM_RERANKER_FAIL_MAX", 3) or 3)
        self.cooldown_secs = float(getattr(settings, "SM_RERANKER_COOLDOWN_SECS", 120) or 120)
        self._fail_count = 0
        self._cooldown_until = 0.0
        self._lock = threading.Lock()
        self._supports_top_n = True
        self._last_status = {
            "backend": "dashscope",
            "success": None,
            "fallback_used": False,
            "elapsed_ms": None,
            "reason": "init",
            "cooldown": False,
            "model": self.model_name,
        }
        log.info(f"DashScopeReranker initialized model={self.model_name}.")

    def get_last_status(self) -> dict:
        return dict(self._last_status)

    def _in_cooldown(self) -> bool:
        now = time.time()
        with self._lock:
            if self._cooldown_until > 0 and now < self._cooldown_until:
                return True
            if self._cooldown_until > 0 and now >= self._cooldown_until:
                self._cooldown_until = 0.0
                self._fail_count = 0
        return False

    def _record_success(self) -> None:
        with self._lock:
            self._fail_count = 0
            self._cooldown_until = 0.0

    def _record_failure(self) -> None:
        with self._lock:
            self._fail_count += 1
            if self._fail_count >= self.fail_threshold:
                self._cooldown_until = time.time() + self.cooldown_secs
                self._fail_count = 0

    def _rerank_impl(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        if not chunks:
            return []
        if self._in_cooldown():
            self._last_status = {
                "backend": "dashscope",
                "success": False,
                "fallback_used": False,
                "elapsed_ms": None,
                "reason": "cooldown",
                "cooldown": True,
                "model": self.model_name,
            }
            log.warning("DashScope reranker in cooldown, skip rerank.")
            return chunks

        log.info(f"Reranking {len(chunks)} chunks with DashScope Rerank API model={self.model_name}.")

        nodes_to_rerank = [
            NodeWithScore(
                node=Node(
                    text=chunk.content,
                    extra_info={"original_chunk": chunk, "original_idx": idx},
                )
            )
            for idx, chunk in enumerate(chunks)
        ]

        t0 = time.perf_counter()
        try:
            if self._supports_top_n:
                try:
                    reranked_nodes = self.reranker.postprocess_nodes(
                        nodes_to_rerank,
                        query_str=query,
                        top_n=len(chunks),
                    )
                except TypeError as exc:
                    if "top_n" not in str(exc):
                        raise
                    self._supports_top_n = False
                    log.warning(
                        "DashScopeRerank.postprocess_nodes does not support top_n; retry without top_n."
                    )
                    reranked_nodes = self.reranker.postprocess_nodes(
                        nodes_to_rerank,
                        query_str=query,
                    )
            else:
                reranked_nodes = self.reranker.postprocess_nodes(
                    nodes_to_rerank,
                    query_str=query,
                )

            # Some SDK versions return only Top-N; append missing chunks in original order.
            if len(reranked_nodes) < len(chunks):
                ranked_idx = {
                    int(node.node.extra_info.get("original_idx", -1))
                    for node in reranked_nodes
                    if node.node is not None and node.node.extra_info is not None
                }
                reranked_nodes.extend(
                    node
                    for node in nodes_to_rerank
                    if int(node.node.extra_info.get("original_idx", -1)) not in ranked_idx
                )

            self._record_success()
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._last_status = {
                "backend": "dashscope",
                "success": True,
                "fallback_used": False,
                "elapsed_ms": elapsed_ms,
                "reason": "ok",
                "cooldown": False,
                "model": self.model_name,
                "input_chunks": len(chunks),
                "output_chunks": len(reranked_nodes),
            }
        except Exception as exc:
            log.warning(f"DashScope rerank failed: {exc}")
            self._record_failure()
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._last_status = {
                "backend": "dashscope",
                "success": False,
                "fallback_used": False,
                "elapsed_ms": elapsed_ms,
                "reason": f"error:{exc}",
                "cooldown": self._in_cooldown(),
                "model": self.model_name,
                "input_chunks": len(chunks),
                "output_chunks": len(chunks),
            }
            return chunks

        return [node.node.extra_info["original_chunk"] for node in reranked_nodes]

    async def rerank(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        return self._rerank_impl(query, chunks)

    def rerank_sync(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        return self._rerank_impl(query, chunks)
