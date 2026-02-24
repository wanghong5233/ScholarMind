"""HTTP client for calling ScholarMind RAG endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import secrets
from typing import Any, Dict, Optional

import httpx
from fastapi_jwt import JwtAccessBearerCookie

from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RAGAnswer:
    """Normalized response from the ScholarMind RAG API."""

    answer: str
    citations: list
    chunks: list
    raw: Dict[str, Any]


class RAGClient:
    """Async client for ScholarMind RAG APIs."""

    def __init__(self, base_url: str, timeout: int = 120) -> None:
        """Configure the HTTP client for ScholarMind APIs.

        Args:
            base_url (str): ScholarMind API base URL.
            timeout (int): Request timeout in seconds.
        """

        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._jwt_helper: Optional[JwtAccessBearerCookie] = None
        if settings.JWT_SECRET_KEY:
            self._jwt_helper = JwtAccessBearerCookie(
                secret_key=settings.JWT_SECRET_KEY,
                auto_error=False,
                access_expires_delta=timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS),
            )
        else:
            if getattr(settings, "STRICT_FAIL_FAST", False):
                raise RuntimeError(
                    "JWT_SECRET_KEY is required for DeepResearch internal service calls in STRICT_FAIL_FAST mode."
                )
            logger.warning("JWT_SECRET_KEY not configured; service JWT will be disabled.")

    async def __aenter__(self) -> "RAGClient":
        """Enter the async context manager.

        Returns:
            RAGClient: Client instance.
        """

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Exit the async context manager and close resources.

        Args:
            exc_type: Exception type.
            exc: Exception instance.
            tb: Traceback instance.
        """

        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""

        await self._client.aclose()

    def _generate_service_token(self, user_id: int) -> Optional[str]:
        """Generate a service JWT for internal Core API calls.

        Args:
            user_id (int): User id to embed in the service token.

        Returns:
            Optional[str]: Encoded JWT, or None if unavailable.
        """
        if not self._jwt_helper:
            if getattr(settings, "STRICT_FAIL_FAST", False):
                raise RuntimeError("Service JWT helper is unavailable in STRICT_FAIL_FAST mode.")
            return None
        subject = {
            "user_id": user_id,
            "user_name": f"deep-research-{user_id}",
            "service_name": "deep_research",
            "token_use": "internal_service",
            "salting": secrets.token_hex(8),
        }
        try:
            return self._jwt_helper.create_access_token(subject=subject)
        except Exception as exc:
            logger.error("Failed to create service JWT: %s", exc, exc_info=True)
            if getattr(settings, "STRICT_FAIL_FAST", False):
                raise RuntimeError(f"Failed to create service JWT: {exc}") from exc
            return None

    def _build_headers(self, user_id: int) -> Dict[str, str]:
        """Build request headers for Core API calls."""
        headers = {"X-User-Id": str(user_id)}
        service_token = self._generate_service_token(user_id)
        if service_token:
            headers["Authorization"] = f"Bearer {service_token}"
        return headers

    @staticmethod
    def _is_retryable_status(status_code: Optional[int]) -> bool:
        """Return True when HTTP status is likely transient."""

        return status_code in {429, 500, 502, 503, 504}

    async def ask(
        self,
        session_id: str,
        question: str,
        user_id: int,
        top_k: Optional[int] = None,
        index_mode: Optional[str] = None,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        persist_history: bool = True,
    ) -> RAGAnswer:
        """Call the base RAG ask endpoint.

        Args:
            session_id (str): ScholarMind session id.
            question (str): User question or prompt.
            user_id (int): ScholarMind user id.
            top_k (Optional[int]): Retrieval top_k override.
            index_mode (Optional[str]): Retrieval index mode.
            llm_provider (Optional[str]): Optional ask-level provider override.
            llm_model (Optional[str]): Optional ask-level model override.
            persist_history (bool): Whether to write this call into chat history.

        Returns:
            RAGAnswer: Normalized answer payload.
        """

        payload: Dict[str, Any] = {"question": question, "stream": False}
        payload["persistHistory"] = bool(persist_history)
        if top_k is not None:
            payload["topK"] = top_k
        if index_mode:
            payload["indexMode"] = index_mode
        provider_override = str(llm_provider or "").strip().lower()
        if provider_override:
            payload["llmProvider"] = provider_override
        model_override = str(llm_model or "").strip()
        if model_override:
            payload["llmModel"] = model_override
        headers = self._build_headers(user_id)
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._client.post(
                    f"/api/sessions/{session_id}/ask",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                return RAGAnswer(
                    answer=data.get("answer", ""),
                    citations=data.get("citations", []),
                    chunks=data.get("chunks", []),
                    raw=data,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response else None
                retryable = self._is_retryable_status(status_code)
                if (not retryable) or attempt >= max_attempts:
                    raise
                wait_seconds = min(4.0, 0.8 * (2 ** (attempt - 1)))
                logger.warning(
                    "RAG ask transient HTTP error, retrying: session=%s status=%s attempt=%s/%s wait=%.1fs",
                    session_id,
                    status_code,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= max_attempts:
                    raise
                wait_seconds = min(4.0, 0.8 * (2 ** (attempt - 1)))
                logger.warning(
                    "RAG ask transient transport error, retrying: session=%s err=%s attempt=%s/%s wait=%.1fs",
                    session_id,
                    exc,
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)

        raise RuntimeError("RAG ask retries exhausted unexpectedly")

    async def compare(
        self,
        session_id: str,
        payload: Dict[str, Any],
        user_id: int,
    ) -> Dict[str, Any]:
        """Call the compare endpoint for cross-document analysis.

        Args:
            session_id (str): ScholarMind session id.
            payload (Dict[str, Any]): Compare request payload.
            user_id (int): ScholarMind user id.

        Returns:
            Dict[str, Any]: Raw response payload.
        """

        headers = self._build_headers(user_id)
        response = await self._client.post(
            f"/api/sessions/{session_id}/compare",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def retrieve(
        self,
        session_id: str,
        query: str,
        user_id: int,
        top_k: Optional[int] = None,
        focus_doc_ids: Optional[list[int]] = None,
        index_mode: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Call the internal retrieval endpoint for pure RAG chunks.

        Args:
            session_id (str): ScholarMind session id.
            query (str): Query text.
            user_id (int): ScholarMind user id.
            top_k (Optional[int]): Retrieval top_k override.
            focus_doc_ids (Optional[list[int]]): Optional focus document ids.
            index_mode (Optional[str]): Retrieval index mode.

        Returns:
            list[Dict[str, Any]]: Retrieved chunks.
        """
        headers = self._build_headers(user_id)
        params: Dict[str, Any] = {
            "q": query,
        }
        if top_k is not None:
            params["top_k"] = top_k
        if focus_doc_ids:
            params["focus_doc_ids"] = ",".join(str(x) for x in focus_doc_ids)
        if index_mode:
            params["index_mode"] = index_mode
        response = await self._client.get(
            f"/api/sessions/{session_id}/retrieve",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def get_context(
        self,
        session_id: str,
        user_id: int,
        question: str = "",
        memory_limit: int = 10,
    ) -> Dict[str, Any]:
        """Fetch a unified context pack for internal LLM prompts.

        Args:
            session_id (str): ScholarMind session id.
            user_id (int): ScholarMind user id.
            question (str): Query text for STM selection.
            memory_limit (int): Max memory items to fetch.

        Returns:
            Dict[str, Any]: Context pack payload.
        """
        headers = self._build_headers(user_id)
        params = {"question": question, "memory_limit": memory_limit}
        response = await self._client.get(
            f"/api/internal/context/{session_id}",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def get_session_detail(self, session_id: str, user_id: int) -> Dict[str, Any]:
        """Fetch session details from the Core API.

        Args:
            session_id (str): ScholarMind session id.
            user_id (int): ScholarMind user id.

        Returns:
            Dict[str, Any]: Session detail payload.
        """

        headers = self._build_headers(user_id)
        response = await self._client.get(f"/api/sessions/{session_id}", headers=headers)
        response.raise_for_status()
        return response.json()

    async def search_online_papers(
        self,
        *,
        kb_id: int,
        query: str,
        user_id: int,
        limit: int = 10,
        year: Optional[str] = None,
        providers: Optional[list[str]] = None,
        rank_by: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Search academic papers via Core API ingestion endpoints.

        Args:
            kb_id (int): Knowledge base id.
            query (str): Query text.
            user_id (int): ScholarMind user id.
            limit (int): Result limit.
            year (Optional[str]): Optional year filter.
            providers (Optional[list[str]]): Provider list (e.g. semantic_scholar/arxiv).
            rank_by (Optional[str]): Ranking strategy.

        Returns:
            list[Dict[str, Any]]: Paper metadata list.
        """

        headers = self._build_headers(user_id)
        payload: Dict[str, Any] = {"query": query, "limit": limit, "year": year or ""}
        if providers:
            payload["providers"] = [str(item).strip() for item in providers if str(item).strip()]
        if rank_by:
            payload["rank_by"] = rank_by
        response = await self._client.post(
            f"/api/knowledgebases/{kb_id}/documents/ingest/search-online",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        return []
