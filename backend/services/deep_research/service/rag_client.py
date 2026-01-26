"""HTTP client for calling ScholarMind RAG endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


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

    async def ask(
        self,
        session_id: str,
        question: str,
        user_id: int,
        top_k: Optional[int] = None,
        index_mode: Optional[str] = None,
    ) -> RAGAnswer:
        """Call the base RAG ask endpoint.

        Args:
            session_id (str): ScholarMind session id.
            question (str): User question or prompt.
            user_id (int): ScholarMind user id.
            top_k (Optional[int]): Retrieval top_k override.
            index_mode (Optional[str]): Retrieval index mode.

        Returns:
            RAGAnswer: Normalized answer payload.
        """

        payload: Dict[str, Any] = {"question": question, "stream": False}
        if top_k is not None:
            payload["topK"] = top_k
        if index_mode:
            payload["indexMode"] = index_mode
        headers = {"X-User-Id": str(user_id)}
        response = await self._client.post(f"/api/sessions/{session_id}/ask", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return RAGAnswer(
            answer=data.get("answer", ""),
            citations=data.get("citations", []),
            chunks=data.get("chunks", []),
            raw=data,
        )

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

        headers = {"X-User-Id": str(user_id)}
        response = await self._client.post(f"/api/sessions/{session_id}/compare", json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
