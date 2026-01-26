"""Idea generation pipeline built on ScholarMind RAG."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from schemas.common import CitationOut
from schemas.idea_generation import (
    IdeaGenerationRequest,
    IdeaGenerationResponse,
    IdeaGenerationStatus,
)
from service.citation_manager import AsyncCitationManagerWrapper, CitationManager
from service.citation_utils import register_rag_citations
from service.rag_client import RAGClient
from service.state_store import StateStore
from utils.language import guess_language


class IdeaGenerationPipeline:
    """Generate research ideas using RAG-grounded prompts."""

    def __init__(self, rag_service_url: str, data_root: str, request_timeout: int) -> None:
        """Prepare pipeline dependencies.

        Args:
            rag_service_url (str): ScholarMind API base URL.
            data_root (str): Root storage path.
            request_timeout (int): HTTP timeout in seconds.
        """

        self._rag_service_url = rag_service_url
        self._data_root = Path(data_root)
        self._request_timeout = request_timeout

    async def run(self, request: IdeaGenerationRequest, user_id: int) -> IdeaGenerationResponse:
        """Run the idea generation workflow.

        Args:
            request (IdeaGenerationRequest): Input payload.
            user_id (int): ScholarMind user id.

        Returns:
            IdeaGenerationResponse: Generated ideas with citations.
        """

        idea_id = self._new_idea_id()
        store = StateStore(self._data_root, idea_id)
        citation_manager = CitationManager(research_id=idea_id, cache_dir=store.root)
        citation_manager_async = AsyncCitationManagerWrapper(citation_manager)

        started_at = datetime.utcnow()
        self._update_meta(
            store,
            {
                "idea_id": idea_id,
                "status": IdeaGenerationStatus.RUNNING.value,
                "topic": request.topic,
                "started_at": started_at.isoformat(),
                "user_id": user_id,
                "request": request.model_dump(mode="json"),
            },
        )

        if not request.session_id:
            response = IdeaGenerationResponse(
                idea_id=idea_id,
                ideas_markdown="Missing session_id; unable to call ScholarMind RAG.",
                citations=[],
                trace={"error": "missing_session_id"},
            )
            self._save_payload(store, response)
            finished_at = datetime.utcnow()
            self._update_meta(
                store,
                {
                    "status": IdeaGenerationStatus.FAILED.value,
                    "finished_at": finished_at.isoformat(),
                    "duration_seconds": (finished_at - started_at).total_seconds(),
                    "error": "missing_session_id",
                },
            )
            return response

        try:
            prompt = self._build_prompt(request)
            async with RAGClient(self._rag_service_url, timeout=self._request_timeout) as rag_client:
                answer = await rag_client.ask(
                    session_id=request.session_id,
                    question=prompt,
                    user_id=user_id,
                    top_k=request.top_k,
                    index_mode=request.index_mode,
                )

            await register_rag_citations(
                rag_citations=answer.citations,
                citation_manager=citation_manager_async,
                source_id="IDEA",
            )
            citation_manager.build_ref_map()

            response = IdeaGenerationResponse(
                idea_id=idea_id,
                ideas_markdown=answer.answer or "No ideas generated yet.",
                citations=[self._to_citation_out(c) for c in citation_manager.list_citations()],
                trace={"prompt": prompt, "raw": answer.raw},
            )

            self._save_payload(store, response)
            finished_at = datetime.utcnow()
            self._update_meta(
                store,
                {
                    "status": IdeaGenerationStatus.COMPLETED.value,
                    "finished_at": finished_at.isoformat(),
                    "duration_seconds": (finished_at - started_at).total_seconds(),
                },
            )
            return response
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures
            finished_at = datetime.utcnow()
            self._update_meta(
                store,
                {
                    "status": IdeaGenerationStatus.FAILED.value,
                    "finished_at": finished_at.isoformat(),
                    "duration_seconds": (finished_at - started_at).total_seconds(),
                    "error": str(exc),
                },
            )
            raise

    def _build_prompt(self, request: IdeaGenerationRequest) -> str:
        """Build the RAG prompt for idea generation.

        Args:
            request (IdeaGenerationRequest): Input payload.

        Returns:
            str: Prompt string.
        """

        language = request.language or guess_language(request.topic)
        constraints_text = ""
        if request.constraints:
            joined = "; ".join(request.constraints)
            constraints_text = f"Constraints: {joined}."

        if language == "zh":
            return (
                f"请围绕主题“{request.topic}”生成 {request.idea_count} 个研究想法。"
                "每个想法包含一句话的创新点和一句话的可行性说明，"
                "如果有出处请附上引用标记（例如 [1]）。"
                f"{constraints_text}"
            )
        return (
            f"Generate {request.idea_count} research ideas for the topic '{request.topic}'. "
            "For each idea, provide one sentence of novelty and one sentence of feasibility. "
            "If sources exist, append citation tags like [1]. "
            f"{constraints_text}"
        )

    @staticmethod
    def _to_citation_out(citation: Any) -> CitationOut:
        """Convert internal citation metadata to API output.

        Args:
            citation (Any): Internal citation object.

        Returns:
            CitationOut: API-ready citation payload.
        """

        return CitationOut(
            citation_id=citation.citation_id,
            ref_number=citation.ref_number,
            title=citation.title,
            url=citation.url,
            snippet=citation.snippet,
            source_type=citation.source_type,
            metadata=citation.metadata,
        )

    @staticmethod
    def _new_idea_id() -> str:
        """Create a short idea generation id.

        Returns:
            str: Generated idea id.
        """

        return f"idea_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _update_meta(store: StateStore, payload: Dict[str, Any]) -> None:
        """Persist or update idea generation metadata."""

        current = store.load_json("idea_meta.json") or {}
        current.update(payload)
        store.save_json("idea_meta.json", current)

    @staticmethod
    def _save_payload(store: StateStore, response: IdeaGenerationResponse) -> None:
        """Persist the idea generation payload."""

        store.save_json("ideas.json", response.model_dump(mode="json"))
