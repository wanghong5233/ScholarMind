"""
HTTP Reranker 实现类（支持本地失败自动兜底）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import threading
import time

import httpx

from core.config import settings
from schemas.rag import Chunk
from service.core.abstractions.reranker import BaseReranker
from utils.get_logger import log


class HttpReranker(BaseReranker):
    """通过 HTTP 调用本地 reranker，并在失败时可兜底到 DashScope。"""

    def __init__(self):
        raw_endpoint = str(getattr(settings, "SM_RERANKER_ENDPOINT", "") or "").strip()
        self.endpoint = raw_endpoint.rstrip("/") if raw_endpoint else ""
        self.local_enabled = bool(self.endpoint)
        self.rerank_url = f"{self.endpoint}/rerank" if self.local_enabled else ""
        self.health_url = f"{self.endpoint}/health" if self.local_enabled else ""
        self.request_timeout = float(getattr(settings, "SM_RERANKER_HTTP_TIMEOUT", 60) or 60)
        self.health_timeout = float(getattr(settings, "SM_RERANKER_HEALTH_TIMEOUT", 5) or 5)
        self.fail_threshold = int(getattr(settings, "SM_RERANKER_FAIL_MAX", 3) or 3)
        self.cooldown_secs = float(getattr(settings, "SM_RERANKER_COOLDOWN_SECS", 120) or 120)
        self.enable_dashscope_fallback = bool(getattr(settings, "SM_RERANKER_FALLBACK_TO_DASHSCOPE", True))
        self._fail_count = 0
        self._cooldown_until = 0.0
        self._lock = threading.Lock()
        self._dashscope_fallback: Optional[BaseReranker] = None
        self._dashscope_init_failed = False
        self._last_status: Dict[str, Any] = {
            "backend": "local_http" if self.local_enabled else "unavailable",
            "success": None,
            "fallback_used": False,
            "elapsed_ms": None,
            "reason": "init",
            "cooldown": False,
        }
        self._service_hint = (
            "请确认 reranker 服务已启动：`docker-compose up -d reranker` "
            f"并可通过 {self.health_url or '<missing-endpoint>'} 访问"
        )

        if self.local_enabled:
            self._perform_health_check()
        elif not self.enable_dashscope_fallback:
            raise ValueError("SM_RERANKER_ENDPOINT is not set, and fallback is disabled.")
        else:
            log.warning(
                "[HTTP_RERANK_INIT] local endpoint is missing, will only use DashScope fallback when available."
            )

    def _set_status(self, **kwargs: Any) -> None:
        with self._lock:
            self._last_status.update(kwargs)

    def get_last_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last_status)

    def _perform_health_check(self) -> None:
        """启动时执行健康检查，并记录模型是否就绪。"""
        try:
            with httpx.Client(timeout=self.health_timeout) as client:
                response = client.get(self.health_url)
                response.raise_for_status()
                health_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                gpu_available = bool(health_data.get("gpu_available", False))
                model_loaded = bool(health_data.get("model_loaded", False))
                model_ready = bool(health_data.get("model_ready", model_loaded))
                log.info(
                    f"[HTTP_RERANK_HEALTH] endpoint={self.endpoint} gpu={gpu_available} "
                    f"model_loaded={model_loaded} model_ready={model_ready}"
                )
                if not model_ready:
                    log.warning(
                        "[HTTP_RERANK_HEALTH_WARN] service reachable but model is not ready yet; "
                        "first rerank may trigger model download/load."
                    )
        except httpx.HTTPStatusError as exc:
            log.warning(
                f"[HTTP_RERANK_HEALTH_FAIL] status={exc.response.status_code} hint={self._service_hint}"
            )
        except httpx.RequestError as exc:
            log.warning(f"[HTTP_RERANK_HEALTH_FAIL] request_error={exc} hint={self._service_hint}")

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

    def _build_request_data(self, query: str, chunks: List[Chunk]) -> Dict[str, Any]:
        return {
            "query": query,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "content": chunk.content,
                    "metadata": chunk.metadata or {},
                }
                for chunk in chunks
            ],
            "batch_size": 32,
        }

    def _rebuild_chunks(self, chunks: List[Chunk], result: Dict[str, Any]) -> List[Chunk]:
        reranked_chunks_data = result.get("reranked_chunks", [])
        scores = result.get("scores", [])
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        reranked_chunks: List[Chunk] = []

        for idx, chunk_data in enumerate(reranked_chunks_data):
            chunk_id = chunk_data.get("chunk_id")
            if chunk_id in chunk_map:
                chunk = chunk_map[chunk_id]
                if idx < len(scores):
                    if chunk.metadata is None:
                        chunk.metadata = {}
                    chunk.metadata["rerank_score"] = float(scores[idx])
                reranked_chunks.append(chunk)
            else:
                log.warning(f"[HTTP_RERANK_WARN] chunk '{chunk_id}' not found in original chunks")

        if reranked_chunks:
            log.info(
                f"[HTTP_RERANK_COMPLETE] top_score={scores[0] if scores else 0:.4f} "
                f"score_min={min(scores) if scores else 0:.4f} score_max={max(scores) if scores else 0:.4f} "
                f"reranked={len(reranked_chunks)}/{len(chunks)}"
            )
        return reranked_chunks

    def _get_dashscope_fallback(self) -> Optional[BaseReranker]:
        if not self.enable_dashscope_fallback:
            return None
        if self._dashscope_init_failed:
            return None
        if self._dashscope_fallback is not None:
            return self._dashscope_fallback
        try:
            from service.core.implementations.rerankers.dashscope import DashScopeReranker
            self._dashscope_fallback = DashScopeReranker()
            log.warning("[RERANK_FALLBACK_READY] DashScope fallback reranker initialized.")
            return self._dashscope_fallback
        except Exception as exc:
            self._dashscope_init_failed = True
            log.warning(f"[RERANK_FALLBACK_UNAVAILABLE] failed to init DashScope fallback: {exc}")
            return None

    def _resolve_fallback_status(
        self,
        *,
        fallback: BaseReranker,
        reranked: List[Chunk],
        measured_elapsed_ms: int,
    ) -> Dict[str, Any]:
        """Normalize fallback status to avoid false-positive 'success' in logs."""
        status_getter = getattr(fallback, "get_last_status", None)
        raw_status: Dict[str, Any] = {}
        if callable(status_getter):
            try:
                raw = status_getter()
                if isinstance(raw, dict):
                    raw_status = raw
            except Exception as exc:  # pragma: no cover - defensive guard
                log.warning(f"[RERANK_FALLBACK_STATUS_WARN] failed to read fallback status: {exc}")

        backend = str(raw_status.get("backend", "dashscope"))
        reason = str(raw_status.get("reason", "ok"))
        cooldown = bool(raw_status.get("cooldown", False))
        success = bool(raw_status.get("success", True))
        elapsed_ms_raw = raw_status.get("elapsed_ms")
        try:
            elapsed_ms = int(elapsed_ms_raw) if elapsed_ms_raw is not None else int(measured_elapsed_ms)
        except Exception:
            elapsed_ms = int(measured_elapsed_ms)

        return {
            "backend": backend,
            "reason": reason,
            "cooldown": cooldown,
            "success": success,
            "elapsed_ms": elapsed_ms,
            "output_chunks": int(raw_status.get("output_chunks", len(reranked))),
        }

    async def _fallback_async(self, *, query: str, chunks: List[Chunk], reason: str, local_elapsed_ms: Optional[int]) -> List[Chunk]:
        fallback = self._get_dashscope_fallback()
        if fallback is None:
            self._set_status(
                backend="original_order",
                success=False,
                fallback_used=False,
                elapsed_ms=local_elapsed_ms,
                reason=f"{reason};dashscope_unavailable",
                cooldown=self._in_cooldown(),
            )
            log.warning(f"[RERANK_FALLBACK_SKIP] reason={reason} fallback=dashscope_unavailable")
            return chunks
        t0 = time.perf_counter()
        try:
            reranked = await fallback.rerank(query, chunks)
            measured_elapsed = int((time.perf_counter() - t0) * 1000)
            fallback_status = self._resolve_fallback_status(
                fallback=fallback,
                reranked=reranked,
                measured_elapsed_ms=measured_elapsed,
            )
            if fallback_status["success"]:
                self._set_status(
                    backend=fallback_status["backend"],
                    success=True,
                    fallback_used=True,
                    elapsed_ms=fallback_status["elapsed_ms"],
                    reason=f"fallback_from:{reason}",
                    cooldown=fallback_status["cooldown"],
                    local_elapsed_ms=local_elapsed_ms,
                    input_chunks=len(chunks),
                    output_chunks=fallback_status["output_chunks"],
                )
                log.warning(
                    f"[RERANK_FALLBACK_OK] target={fallback_status['backend']} reason={reason} "
                    f"input={len(chunks)} output={fallback_status['output_chunks']} "
                    f"elapsed_ms={fallback_status['elapsed_ms']}"
                )
            else:
                self._set_status(
                    backend=fallback_status["backend"],
                    success=False,
                    fallback_used=True,
                    elapsed_ms=fallback_status["elapsed_ms"],
                    reason=f"fallback_failed_from:{reason};{fallback_status['reason']}",
                    cooldown=fallback_status["cooldown"],
                    local_elapsed_ms=local_elapsed_ms,
                    input_chunks=len(chunks),
                    output_chunks=fallback_status["output_chunks"],
                )
                log.warning(
                    f"[RERANK_FALLBACK_FAIL] target={fallback_status['backend']} reason={reason} "
                    f"fallback_reason={fallback_status['reason']}"
                )
            return reranked
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            self._set_status(
                backend="original_order",
                success=False,
                fallback_used=False,
                elapsed_ms=elapsed,
                reason=f"{reason};dashscope_error:{exc}",
                cooldown=self._in_cooldown(),
                local_elapsed_ms=local_elapsed_ms,
            )
            log.warning(f"[RERANK_FALLBACK_FAIL] target=dashscope reason={reason} error={exc}")
            return chunks

    def _fallback_sync(self, *, query: str, chunks: List[Chunk], reason: str, local_elapsed_ms: Optional[int]) -> List[Chunk]:
        fallback = self._get_dashscope_fallback()
        if fallback is None:
            self._set_status(
                backend="original_order",
                success=False,
                fallback_used=False,
                elapsed_ms=local_elapsed_ms,
                reason=f"{reason};dashscope_unavailable",
                cooldown=self._in_cooldown(),
            )
            log.warning(f"[RERANK_FALLBACK_SKIP] reason={reason} fallback=dashscope_unavailable")
            return chunks
        t0 = time.perf_counter()
        try:
            reranked = fallback.rerank_sync(query, chunks)
            measured_elapsed = int((time.perf_counter() - t0) * 1000)
            fallback_status = self._resolve_fallback_status(
                fallback=fallback,
                reranked=reranked,
                measured_elapsed_ms=measured_elapsed,
            )
            if fallback_status["success"]:
                self._set_status(
                    backend=fallback_status["backend"],
                    success=True,
                    fallback_used=True,
                    elapsed_ms=fallback_status["elapsed_ms"],
                    reason=f"fallback_from:{reason}",
                    cooldown=fallback_status["cooldown"],
                    local_elapsed_ms=local_elapsed_ms,
                    input_chunks=len(chunks),
                    output_chunks=fallback_status["output_chunks"],
                )
                log.warning(
                    f"[RERANK_FALLBACK_OK] target={fallback_status['backend']} reason={reason} "
                    f"input={len(chunks)} output={fallback_status['output_chunks']} "
                    f"elapsed_ms={fallback_status['elapsed_ms']}"
                )
            else:
                self._set_status(
                    backend=fallback_status["backend"],
                    success=False,
                    fallback_used=True,
                    elapsed_ms=fallback_status["elapsed_ms"],
                    reason=f"fallback_failed_from:{reason};{fallback_status['reason']}",
                    cooldown=fallback_status["cooldown"],
                    local_elapsed_ms=local_elapsed_ms,
                    input_chunks=len(chunks),
                    output_chunks=fallback_status["output_chunks"],
                )
                log.warning(
                    f"[RERANK_FALLBACK_FAIL] target={fallback_status['backend']} reason={reason} "
                    f"fallback_reason={fallback_status['reason']}"
                )
            return reranked
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            self._set_status(
                backend="original_order",
                success=False,
                fallback_used=False,
                elapsed_ms=elapsed,
                reason=f"{reason};dashscope_error:{exc}",
                cooldown=self._in_cooldown(),
                local_elapsed_ms=local_elapsed_ms,
            )
            log.warning(f"[RERANK_FALLBACK_FAIL] target=dashscope reason={reason} error={exc}")
            return chunks

    async def rerank(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        if not chunks:
            return []
        if not self.local_enabled:
            return await self._fallback_async(
                query=query,
                chunks=chunks,
                reason="local_endpoint_missing",
                local_elapsed_ms=None,
            )
        if self._in_cooldown():
            log.warning("[HTTP_RERANK_COOLDOWN] local reranker is in cooldown, switching to fallback.")
            return await self._fallback_async(
                query=query,
                chunks=chunks,
                reason="local_cooldown",
                local_elapsed_ms=None,
            )

        request_data = self._build_request_data(query, chunks)
        log.info(f"[HTTP_RERANK_REQUEST] query='{query[:60]}...' chunks={len(chunks)} endpoint={self.rerank_url}")
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.post(self.rerank_url, json=request_data)
                response.raise_for_status()
                result = response.json()
            reranked = self._rebuild_chunks(chunks, result)
            elapsed = int((time.perf_counter() - t0) * 1000)
            self._record_success()
            self._set_status(
                backend="local_http",
                success=True,
                fallback_used=False,
                elapsed_ms=elapsed,
                reason="ok",
                cooldown=False,
                input_chunks=len(chunks),
                output_chunks=len(reranked),
            )
            return reranked
        except httpx.HTTPStatusError as exc:
            log.error(
                f"[HTTP_RERANK_FAIL] type=http_status status={exc.response.status_code} "
                f"body={exc.response.text[:300]}"
            )
            failure_reason = f"http_status:{exc.response.status_code}"
        except httpx.RequestError as exc:
            log.error(f"[HTTP_RERANK_FAIL] type=request_error error={exc}")
            failure_reason = f"request_error:{exc}"
        except Exception as exc:
            log.exception(f"[HTTP_RERANK_FAIL] type=unexpected error={exc}")
            failure_reason = f"unexpected:{exc}"

        elapsed = int((time.perf_counter() - t0) * 1000)
        self._record_failure()
        self._set_status(
            backend="local_http",
            success=False,
            fallback_used=False,
            elapsed_ms=elapsed,
            reason=failure_reason,
            cooldown=self._in_cooldown(),
            input_chunks=len(chunks),
            output_chunks=len(chunks),
        )
        return await self._fallback_async(
            query=query,
            chunks=chunks,
            reason=failure_reason,
            local_elapsed_ms=elapsed,
        )

    def rerank_sync(self, query: str, chunks: List[Chunk]) -> List[Chunk]:
        if not chunks:
            return []
        if not self.local_enabled:
            return self._fallback_sync(
                query=query,
                chunks=chunks,
                reason="local_endpoint_missing",
                local_elapsed_ms=None,
            )
        if self._in_cooldown():
            log.warning("[HTTP_RERANK_COOLDOWN] local reranker is in cooldown, switching to fallback.")
            return self._fallback_sync(
                query=query,
                chunks=chunks,
                reason="local_cooldown",
                local_elapsed_ms=None,
            )

        request_data = self._build_request_data(query, chunks)
        log.info(f"[HTTP_RERANK_REQUEST_SYNC] query='{query[:60]}...' chunks={len(chunks)} endpoint={self.rerank_url}")
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self.request_timeout) as client:
                response = client.post(self.rerank_url, json=request_data)
                response.raise_for_status()
                result = response.json()
            reranked = self._rebuild_chunks(chunks, result)
            elapsed = int((time.perf_counter() - t0) * 1000)
            self._record_success()
            self._set_status(
                backend="local_http",
                success=True,
                fallback_used=False,
                elapsed_ms=elapsed,
                reason="ok",
                cooldown=False,
                input_chunks=len(chunks),
                output_chunks=len(reranked),
            )
            return reranked
        except httpx.HTTPStatusError as exc:
            log.error(
                f"[HTTP_RERANK_FAIL_SYNC] type=http_status status={exc.response.status_code} "
                f"body={exc.response.text[:300]}"
            )
            failure_reason = f"http_status:{exc.response.status_code}"
        except httpx.RequestError as exc:
            log.error(f"[HTTP_RERANK_FAIL_SYNC] type=request_error error={exc}")
            failure_reason = f"request_error:{exc}"
        except Exception as exc:
            log.exception(f"[HTTP_RERANK_FAIL_SYNC] type=unexpected error={exc}")
            failure_reason = f"unexpected:{exc}"

        elapsed = int((time.perf_counter() - t0) * 1000)
        self._record_failure()
        self._set_status(
            backend="local_http",
            success=False,
            fallback_used=False,
            elapsed_ms=elapsed,
            reason=failure_reason,
            cooldown=self._in_cooldown(),
            input_chunks=len(chunks),
            output_chunks=len(chunks),
        )
        return self._fallback_sync(
            query=query,
            chunks=chunks,
            reason=failure_reason,
            local_elapsed_ms=elapsed,
        )

