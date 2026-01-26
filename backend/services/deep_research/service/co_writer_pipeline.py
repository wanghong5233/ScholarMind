"""Co-writer pipeline for rewriting or expanding text with RAG grounding."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from schemas.co_writer import CoWriterRequest, CoWriterResponse, CoWriterStatus, CoWriterTask
from schemas.common import CitationOut
from service.citation_manager import AsyncCitationManagerWrapper, CitationManager
from service.citation_utils import register_rag_citations
from service.rag_client import RAGClient
from service.state_store import StateStore
from utils.language import guess_language


class CoWriterPipeline:
    """Rewrite or expand text using ScholarMind RAG."""

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

    async def run(self, request: CoWriterRequest, user_id: int) -> CoWriterResponse:
        """Run the co-writer workflow.

        Args:
            request (CoWriterRequest): Input payload.
            user_id (int): ScholarMind user id.

        Returns:
            CoWriterResponse: Generated output with citations.
        """

        operation_id = self._new_operation_id()
        store = StateStore(self._data_root, operation_id)
        citation_manager = CitationManager(research_id=operation_id, cache_dir=store.root)
        citation_manager_async = AsyncCitationManagerWrapper(citation_manager)

        started_at = datetime.utcnow()
        self._update_meta(
            store,
            {
                "operation_id": operation_id,
                "status": CoWriterStatus.RUNNING.value,
                "task": request.task.value,
                "started_at": started_at.isoformat(),
                "user_id": user_id,
                "request": request.model_dump(mode="json"),
            },
        )

        if not request.session_id:
            response = CoWriterResponse(
                operation_id=operation_id,
                result_markdown="Missing session_id; unable to call ScholarMind RAG.",
                citations=[],
                trace={"error": "missing_session_id"},
            )
            self._save_payload(store, response)
            finished_at = datetime.utcnow()
            self._update_meta(
                store,
                {
                    "status": CoWriterStatus.FAILED.value,
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
                source_id="COWRITER",
            )
            citation_manager.build_ref_map()

            response = CoWriterResponse(
                operation_id=operation_id,
                result_markdown=answer.answer or "No output generated yet.",
                citations=[self._to_citation_out(c) for c in citation_manager.list_citations()],
                trace={"prompt": prompt, "raw": answer.raw},
            )

            self._save_payload(store, response)
            finished_at = datetime.utcnow()
            self._update_meta(
                store,
                {
                    "status": CoWriterStatus.COMPLETED.value,
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
                    "status": CoWriterStatus.FAILED.value,
                    "finished_at": finished_at.isoformat(),
                    "duration_seconds": (finished_at - started_at).total_seconds(),
                    "error": str(exc),
                },
            )
            raise

    def _build_prompt(self, request: CoWriterRequest) -> str:
        """Build the RAG prompt for co-writer tasks.

        Args:
            request (CoWriterRequest): Input payload.

        Returns:
            str: Prompt string.
        """

        language = request.language or guess_language(request.text)
        instruction = request.instructions or ""
        tone = f"Tone: {request.tone}." if request.tone else ""
        task_hint = self._task_hint(request.task, language)

        if language == "zh":
            return (
                f"{task_hint}\n"
                f"{tone}\n"
                f"{instruction}\n"
                "请处理以下文本，并在需要时附上引用标记（如 [1]）。\n"
                f"原文：\n{request.text}"
            )
        return (
            f"{task_hint}\n"
            f"{tone}\n"
            f"{instruction}\n"
            "Process the text below and add citation tags like [1] when needed.\n"
            f"Text:\n{request.text}"
        )

    @staticmethod
    def _task_hint(task: CoWriterTask, language: str) -> str:
        """Return a localized instruction for the task.

        Args:
            task (CoWriterTask): Requested co-writer task.
            language (str): Language code.

        Returns:
            str: Localized task instruction.
        """

        if language == "zh":
            return {
                CoWriterTask.REWRITE: "请改写以下内容，保持学术风格。",
                CoWriterTask.EXPAND: "请扩展以下内容，补充必要的背景和细节。",
                CoWriterTask.SHORTEN: "请压缩以下内容，保留关键信息。",
                CoWriterTask.ANNOTATE: "请为以下内容添加要点式注释。",
            }[task]
        return {
            CoWriterTask.REWRITE: "Rewrite the text in an academic tone.",
            CoWriterTask.EXPAND: "Expand the text with background and details.",
            CoWriterTask.SHORTEN: "Shorten the text while preserving key points.",
            CoWriterTask.ANNOTATE: "Annotate the text with bullet-style notes.",
        }[task]

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
    def _new_operation_id() -> str:
        """Create a short operation id.

        Returns:
            str: Generated operation id.
        """

        return f"cow_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _update_meta(store: StateStore, payload: Dict[str, Any]) -> None:
        """Persist or update co-writer metadata."""

        current = store.load_json("co_writer_meta.json") or {}
        current.update(payload)
        store.save_json("co_writer_meta.json", current)

    @staticmethod
    def _save_payload(store: StateStore, response: CoWriterResponse) -> None:
        """Persist the co-writer payload."""

        store.save_json("co_writer.json", response.model_dump(mode="json"))
