"""DeepResearch orchestration pipeline (planning → researching → reporting)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agents.planner_agent import PlanItem, PlannerAgent
from agents.note_agent import NoteAgent
from agents.manager_agent import AsyncManagerAgentWrapper, ManagerAgent
from agents.decision_agent import DecisionAgent
from agents.research_agent import ResearchAgent
from agents.reporter_agent import ReporterAgent
from schemas.common import (
    CitationOut,
    DeepResearchRequest,
    DeepResearchResponse,
    DeepResearchStatus,
)
from service.citation_manager import AsyncCitationManagerWrapper, CitationManager
from service.llm_client import LLMClient
from service.data_structures import DynamicTopicQueue, TopicStatus
from service.code_exec_client import CodeExecClient
from service.rag_client import RAGClient
from service.report_refiner import ReportRefiner
from service.report_quality import analyze_report
from service.report_sanitizer import sanitize_citations
from service.report_templates import ReportTemplateBuilder
from service.state_store import StateStore
from service.token_usage import TokenUsageTracker
from service.tool_registry import create_tool_registry
from service.tool_router import ToolRouter
from service.web_search_client import WebSearchClient
from utils.language import guess_language
from config import settings


class ResearchPipeline:
    """Queue-based DeepResearch pipeline."""

    def __init__(self, rag_service_url: str, data_root: str, request_timeout: int) -> None:
        """Prepare pipeline dependencies.

        Args:
            rag_service_url (str): ScholarMind API base URL.
            data_root (str): Root path for research state storage.
            request_timeout (int): HTTP timeout in seconds.
        """

        self._rag_service_url = rag_service_url
        self._data_root = Path(data_root)
        self._request_timeout = request_timeout

    async def run(
        self,
        request: DeepResearchRequest,
        user_id: int,
        research_id: Optional[str] = None,
        resume: bool = False,
    ) -> DeepResearchResponse:
        """Run a DeepResearch session end-to-end.

        Args:
            request (DeepResearchRequest): DeepResearch input payload.
            user_id (int): ScholarMind user id.

        Returns:
            DeepResearchResponse: Report and citation metadata.
        """

        research_id = research_id or self._new_research_id()
        store = StateStore(self._data_root, research_id)
        started_at = datetime.utcnow()
        plan_payload: Dict[str, Any] = {"items": []}
        existing_meta = store.load_meta() or {}
        token_tracker = TokenUsageTracker.from_settings(existing_meta.get("token_usage"))

        if resume:
            meta = existing_meta
            if not meta:
                raise ValueError("Research meta not found for resume.")
            started_at = self._parse_started_at(meta.get("started_at")) or started_at
            resume_count = int(meta.get("resume_count") or 0) + 1
            store.update_meta(
                {
                    "status": DeepResearchStatus.RUNNING.value,
                    "resumed_at": datetime.utcnow().isoformat(),
                    "resume_count": resume_count,
                    "resume_pending": False,
                }
            )
            queue, citation_manager = self._load_resume_state(store)
            citation_manager_async = AsyncCitationManagerWrapper(citation_manager)
            plan_payload = store.load_json("outline.json") or {"items": []}
            store.save_json("queue.json", queue.to_dict())
            store.save_json("citations.json", citation_manager.to_dict())
            self._emit_progress(
                store,
                research_id,
                "researching",
                "Run resumed",
                {"pending": len(queue.list_blocks(status=TopicStatus.PENDING))},
            )
            manager_async = AsyncManagerAgentWrapper(
                ManagerAgent(
                    queue=queue,
                    max_depth=request.depth,
                    max_followups=settings.MAX_FOLLOWUPS_PER_BLOCK,
                    min_summary_chars=settings.FOLLOWUP_TRIGGER_MIN_CHARS,
                )
            )
            language = request.language or guess_language(request.topic)
            context_text, context_meta = await self._load_context_pack(request, user_id)
            if context_meta:
                store.update_meta({"context": context_meta})
            try:
                await self._research_queue(
                    store=store,
                    queue=queue,
                    request=request,
                    user_id=user_id,
                    citation_manager=citation_manager,
                    citation_manager_async=citation_manager_async,
                    manager_async=manager_async,
                    language=language,
                    context_text=context_text,
                    token_tracker=token_tracker,
                )
            except Exception as exc:  # noqa: BLE001 - record run meta on failure
                finished_at = datetime.utcnow()
                store.update_meta(
                    {
                        "status": DeepResearchStatus.FAILED.value,
                        "finished_at": finished_at.isoformat(),
                        "duration_seconds": (finished_at - started_at).total_seconds(),
                        "error": str(exc),
                        "token_usage": token_tracker.summary(),
                    }
                )
                raise
        else:
            priority = existing_meta.get("priority")
            if priority is None and isinstance(request.metadata, dict):
                priority = request.metadata.get("priority")
            meta_payload = {
                **existing_meta,
                "research_id": research_id,
                "status": DeepResearchStatus.RUNNING.value,
                "topic": request.topic,
                "mode": request.mode.value,
                "priority": priority,
                "started_at": started_at.isoformat(),
                "user_id": user_id,
                "request": request.model_dump(mode="json"),
                "resume_pending": False,
            }
            store.save_meta(meta_payload)
            citation_manager = CitationManager(research_id=research_id, cache_dir=store.root)
            citation_manager_async = AsyncCitationManagerWrapper(citation_manager)

            queue = DynamicTopicQueue(research_id=research_id)
            root_block = queue.add_block(title=request.topic, question=request.topic, depth=0)
            queue.mark_block_status(root_block.block_id, TopicStatus.RESEARCHING)

            plan_items: List[PlanItem] = []
            try:
                plan_items = await self._build_plan(request, user_id)
                queue = self._apply_plan(queue, root_block.block_id, plan_items, request.max_iterations)
                queue.mark_block_status(root_block.block_id, TopicStatus.COMPLETED)

                manager = ManagerAgent(
                    queue=queue,
                    max_depth=request.depth,
                    max_followups=settings.MAX_FOLLOWUPS_PER_BLOCK,
                    min_summary_chars=settings.FOLLOWUP_TRIGGER_MIN_CHARS,
                )
                manager_async = AsyncManagerAgentWrapper(manager)

                plan_payload = {"items": [self._plan_item_to_dict(i) for i in plan_items]}
                store.save_json("outline.json", plan_payload)
                store.save_json("queue.json", queue.to_dict())
                store.save_json("citations.json", citation_manager.to_dict())
                store.save_json(
                    "step1_planning.json",
                    {
                        "research_id": research_id,
                        "generated_at": datetime.utcnow().isoformat(),
                        "plan": plan_payload,
                        "queue": queue.to_dict(),
                        "citations": citation_manager.to_dict(),
                    },
                )
                self._emit_progress(
                    store, research_id, "planning", "Planning completed", {"items": len(plan_items)}
                )

                language = request.language or guess_language(request.topic)
                context_text, context_meta = await self._load_context_pack(request, user_id)
                if context_meta:
                    store.update_meta({"context": context_meta})
                await self._research_queue(
                    store=store,
                    queue=queue,
                    request=request,
                    user_id=user_id,
                    citation_manager=citation_manager,
                    citation_manager_async=citation_manager_async,
                    manager_async=manager_async,
                    language=language,
                    context_text=context_text,
                    token_tracker=token_tracker,
                )
            except Exception as exc:  # noqa: BLE001 - record run meta on failure
                finished_at = datetime.utcnow()
                store.update_meta(
                    {
                        "status": DeepResearchStatus.FAILED.value,
                        "finished_at": finished_at.isoformat(),
                        "duration_seconds": (finished_at - started_at).total_seconds(),
                        "error": str(exc),
                        "token_usage": token_tracker.summary(),
                    }
                )
                raise

        store.save_json(
            "step2_research.json",
            {
                "research_id": research_id,
                "generated_at": datetime.utcnow().isoformat(),
                "queue": queue.to_dict(),
                "citations": citation_manager.to_dict(),
            },
        )

        reporter = ReporterAgent(citation_manager, language=language)
        self._emit_progress(
            store,
            research_id,
            "reporting",
            "Report generation started",
            {"blocks": len(queue.list_blocks())},
        )
        try:
            report = reporter.build_report(request.topic, queue)
            draft_report = report
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Draft report generated",
                {"chars": len(report)},
            )
            report_outline = reporter.build_outline(queue)
            outline_detailed = reporter.build_detailed_outline(queue)
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Outline generated",
                {"sections": len(report_outline), "detailed_lines": len(outline_detailed)},
            )
            report_notes = reporter.build_note_feed(queue)
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Notes compiled",
                {"notes": len(report_notes)},
            )
            citation_table = reporter.build_citation_table()
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Citation table generated",
                {"citations": len(citation_table)},
            )

            allowed_refs = reporter.allowed_reference_numbers()

            llm_report: Optional[str] = None
            if getattr(settings, "REPORT_LLM_SECTIONAL", False):
                llm_report = await self._refine_report_sectional(
                    store=store,
                    reporter=reporter,
                    research_id=research_id,
                    topic=request.topic,
                    language=language,
                    outline=outline_detailed or report_outline,
                    report_outline=report_outline,
                    outline_detailed=outline_detailed,
                    notes=report_notes,
                    citation_table=citation_table,
                    allowed_refs=allowed_refs,
                    draft_report=draft_report,
                    report_style=request.report_style,
                    context_text=context_text,
                    usage_callback=token_tracker.record,
                )
            else:
                llm_report = await self._refine_report(
                    topic=request.topic,
                    language=language,
                    outline=outline_detailed or report_outline,
                    notes=report_notes,
                    citation_table=citation_table,
                    report_style=request.report_style,
                    context_text=context_text,
                    usage_callback=token_tracker.record,
                )
            if llm_report:
                if not getattr(settings, "REPORT_LLM_SECTIONAL", False):
                    self._emit_progress(
                        store,
                        research_id,
                        "reporting",
                        "LLM refinement completed",
                        {"chars": len(llm_report)},
                    )
                    report = sanitize_citations(llm_report, allowed_refs)
                    report = reporter.append_references_if_missing(report)
                else:
                    report = llm_report
                llm_quality = analyze_report(report)
                if allowed_refs and (llm_quality.get("citations_mentions") or 0) == 0:
                    # Guardrail: if the LLM report contains no citations at all, it breaks the
                    # core "academic-grade + grounded" promise. Fall back to the deterministic
                    # draft report which always includes evidence links.
                    report = draft_report
                    self._emit_progress(
                        store,
                        research_id,
                        "reporting",
                        "LLM report rejected (no citations); using draft report",
                        {"allowed_refs": len(allowed_refs)},
                    )
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Report finalized",
                {"citations": len(citation_manager.list_citations())},
            )
            report_quality = analyze_report(report)
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Report quality analyzed",
                {
                    "paragraphs_total": report_quality.get("paragraphs_total"),
                    "paragraphs_with_citations": report_quality.get("paragraphs_with_citations"),
                    "paragraphs_without_citations": report_quality.get("paragraphs_without_citations"),
                    "citation_paragraph_coverage": report_quality.get("citation_paragraph_coverage"),
                    "citations_mentions": report_quality.get("citations_mentions"),
                    "sections_without_citations": report_quality.get("sections_without_citations") or [],
                },
            )
            summary = self._build_run_summary(queue, citation_manager)
            report_details = {
                "outline": report_outline,
                "outline_detailed": outline_detailed,
                "notes": report_notes,
                "citation_table": citation_table,
                "draft_markdown": draft_report,
                "quality": report_quality,
            }
            trace = {
                "mode": request.mode.value,
                "queue": queue.to_dict(),
                "summary": summary,
                "plan": plan_payload,
                "report_details": report_details,
            }

            store.save_json(
                "report.json",
                {
                    "research_id": research_id,
                    "status": DeepResearchStatus.COMPLETED.value,
                    "report_markdown": report,
                    "outline": report_outline,
                    "notes": report_notes,
                    "citation_table": citation_table,
                    "draft_markdown": draft_report,
                    "summary": summary,
                    "trace": trace,
                    "report_details": report_details,
                },
            )
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Reporting completed",
                {"summary": summary},
            )

            finished_at = datetime.utcnow()
            store.update_meta(
                {
                    "status": DeepResearchStatus.COMPLETED.value,
                    "finished_at": finished_at.isoformat(),
                    "duration_seconds": (finished_at - started_at).total_seconds(),
                    "summary": summary,
                    "token_usage": token_tracker.summary(),
                }
            )

            citation_manager.build_ref_map()
            return DeepResearchResponse(
                research_id=research_id,
                status=DeepResearchStatus.COMPLETED,
                report_markdown=report,
                citations=[self._to_citation_out(c) for c in citation_manager.list_citations()],
                trace=trace,
            )
        except Exception as exc:  # noqa: BLE001 - record run meta on failure
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Reporting failed",
                {"error": str(exc)},
            )
            finished_at = datetime.utcnow()
            store.update_meta(
                {
                    "status": DeepResearchStatus.FAILED.value,
                    "finished_at": finished_at.isoformat(),
                    "duration_seconds": (finished_at - started_at).total_seconds(),
                    "error": str(exc),
                    "token_usage": token_tracker.summary(),
                }
            )
            raise

    async def _build_plan(self, request: DeepResearchRequest, user_id: int) -> List[PlanItem]:
        """Generate plan items using the planner agent.

        Args:
            request (DeepResearchRequest): DeepResearch request.
            user_id (int): ScholarMind user id.

        Returns:
            List[PlanItem]: Planned topic items.
        """

        planner = PlannerAgent(
            depth=request.depth,
            breadth=request.breadth,
            language=request.language,
        )
        if not request.session_id:
            return planner.plan(request.topic)

        try:
            async with RAGClient(self._rag_service_url, timeout=self._request_timeout) as rag_client:
                return await planner.plan_with_rag(
                    topic=request.topic,
                    rag_client=rag_client,
                    session_id=request.session_id,
                    user_id=user_id,
                    top_k=request.top_k,
                    index_mode=request.index_mode,
                )
        except Exception:  # noqa: BLE001 - fallback to template planning
            return planner.plan(request.topic)

    async def _load_context_pack(
        self,
        request: DeepResearchRequest,
        user_id: int,
    ) -> tuple[Optional[str], Dict[str, Any]]:
        """Load a unified conversation context pack and metadata.

        Args:
            request (DeepResearchRequest): Request payload.
            user_id (int): ScholarMind user id.

        Returns:
            tuple[Optional[str], Dict[str, Any]]: Context text and metadata.
        """
        note_context, note_meta = self._extract_metadata_context(request)
        if not request.session_id:
            if note_context:
                context_meta = {
                    "context_present": True,
                    "context_chars": len(note_context),
                    "context_sha256": hashlib.sha256(note_context.encode("utf-8")).hexdigest(),
                    "reason": "metadata_only",
                    **note_meta,
                }
                return note_context, context_meta
            return None, {"context_present": False, "reason": "missing_session_id"}
        try:
            async with RAGClient(self._rag_service_url, timeout=self._request_timeout) as rag_client:
                payload = await rag_client.get_context(
                    session_id=request.session_id,
                    user_id=user_id,
                    question=request.topic,
                )
            context_text_raw = payload.get("context_text")
            context_text = context_text_raw.strip() if isinstance(context_text_raw, str) else ""
            history_items = payload.get("history") or []
            memory_items = (payload.get("memory") or {}).get("items") or []
            if note_context:
                if context_text:
                    context_text = f"{note_context}\n\n{context_text}"
                else:
                    context_text = note_context
            truncated = False
            if context_text and settings.RESEARCH_CONTEXT_MAX_CHARS > 0:
                if len(context_text) > settings.RESEARCH_CONTEXT_MAX_CHARS:
                    context_text = context_text[: settings.RESEARCH_CONTEXT_MAX_CHARS]
                    truncated = True
            context_meta = {
                "context_present": bool(context_text),
                "context_chars": len(context_text),
                "context_sha256": (
                    hashlib.sha256(context_text.encode("utf-8")).hexdigest()
                    if context_text
                    else None
                ),
                "history_count": len(history_items),
                "memory_count": len(memory_items),
                "context_truncated": truncated,
            }
            if note_meta:
                context_meta.update(note_meta)
            return context_text or None, context_meta
        except Exception:
            return None, {"context_present": False, "reason": "fetch_failed"}

    @staticmethod
    def _extract_metadata_context(request: DeepResearchRequest) -> tuple[str, Dict[str, Any]]:
        """Extract optional context overrides from request metadata.

        Args:
            request (DeepResearchRequest): Incoming DeepResearch request.

        Returns:
            tuple[str, Dict[str, Any]]: Context text and metadata details.
        """
        if not isinstance(request.metadata, dict):
            return "", {}
        raw_text = request.metadata.get("context_text")
        if not isinstance(raw_text, str):
            return "", {}
        context_text = raw_text.strip()
        if not context_text:
            return "", {}
        truncated = False
        if settings.NOTEBOOK_MAX_SELECTION_CHARS > 0:
            if len(context_text) > settings.NOTEBOOK_MAX_SELECTION_CHARS:
                context_text = context_text[: settings.NOTEBOOK_MAX_SELECTION_CHARS]
                truncated = True
        return context_text, {
            "note_context_present": True,
            "note_context_chars": len(context_text),
            "note_context_truncated": truncated,
            "note_context_source": request.metadata.get("context_source"),
            "note_context_title": request.metadata.get("context_title"),
            "note_context_id": request.metadata.get("context_note_id"),
        }

    def _apply_plan(
        self,
        queue: DynamicTopicQueue,
        root_id: str,
        plan_items: List[PlanItem],
        max_iterations: int,
    ) -> DynamicTopicQueue:
        """Insert plan items into the queue with parent-child mapping.

        Args:
            queue (DynamicTopicQueue): Queue to mutate.
            root_id (str): Root block id.
            plan_items (List[PlanItem]): Planned items.
            max_iterations (int): Iteration cap per block.

        Returns:
            DynamicTopicQueue: Updated queue.
        """

        parent_map: Dict[str, str] = {}
        for item in plan_items:
            if item.depth != 1:
                continue
            block = queue.add_block(
                title=item.title,
                question=item.question,
                depth=1,
                parent_id=root_id,
                max_iterations=max_iterations,
            )
            parent_map[item.title] = block.block_id

        for item in plan_items:
            if item.depth <= 1:
                continue
            parent_id = parent_map.get(item.parent_title or "")
            queue.add_block(
                title=item.title,
                question=item.question,
                depth=item.depth,
                parent_id=parent_id or root_id,
                max_iterations=max_iterations,
            )
        return queue

    @staticmethod
    def _parse_started_at(value: Any) -> Optional[datetime]:
        """Parse a stored timestamp into a datetime instance."""

        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    def _load_resume_state(self, store: StateStore) -> tuple[DynamicTopicQueue, CitationManager]:
        """Load queue and citation state for resuming a run."""

        queue_payload = store.load_json("queue.json")
        if not queue_payload:
            raise ValueError("Queue snapshot not found for resume.")
        queue = DynamicTopicQueue.from_dict(queue_payload)
        for block in queue.list_blocks():
            if block.status == TopicStatus.RESEARCHING:
                block.status = TopicStatus.PENDING
                block.touch()
            elif block.status == TopicStatus.FAILED and block.iterations < block.max_iterations:
                block.status = TopicStatus.PENDING
                block.touch()
        citations_payload = store.load_json("citations.json")
        if citations_payload:
            citation_manager = CitationManager.from_dict(citations_payload, cache_dir=store.root)
        else:
            citation_manager = CitationManager(research_id=queue.research_id, cache_dir=store.root)
        return queue, citation_manager

    async def _research_queue(
        self,
        *,
        store: StateStore,
        queue: DynamicTopicQueue,
        request: DeepResearchRequest,
        user_id: int,
        citation_manager: CitationManager,
        citation_manager_async: AsyncCitationManagerWrapper,
        manager_async: AsyncManagerAgentWrapper,
        language: str,
        context_text: Optional[str] = None,
        token_tracker: Optional[TokenUsageTracker] = None,
    ) -> None:
        """Run research over all queued blocks.

        Args:
            store (StateStore): State persistence helper.
            queue (DynamicTopicQueue): Topic queue.
            request (DeepResearchRequest): Request payload.
            user_id (int): ScholarMind user id.
            citation_manager (CitationManager): Citation registry.
            citation_manager_async (AsyncCitationManagerWrapper): Async citation wrapper.
            context_text (Optional[str]): Optional conversation context.
            token_tracker (Optional[TokenUsageTracker]): Token usage tracker.
        """

        if not request.session_id:
            blocks = [b for b in queue.list_blocks() if b.depth > 0]
            for block in blocks:
                block.notes.append("Missing session_id; unable to call ScholarMind RAG.")
                await manager_async.mark_status(block.block_id, TopicStatus.SKIPPED)
            store.save_json("queue.json", queue.to_dict())
            self._emit_progress(
                store,
                queue.research_id,
                "researching",
                "Skipped research due to missing session_id",
                {"blocks": len(blocks)},
            )
            return

        iteration_mode = (request.iteration_mode or "legacy").lower()
        if iteration_mode not in {"fixed", "flexible"}:
            iteration_mode = "legacy"
        usage_callback = token_tracker.record if token_tracker else None
        semaphore = asyncio.Semaphore(request.max_parallel)
        self._emit_progress(
            store,
            queue.research_id,
            "researching",
            "Research started",
            {"blocks": len(queue.list_blocks())},
        )

        async with RAGClient(self._rag_service_url, timeout=self._request_timeout) as rag_client:
            web_search_client = None
            try:
                if settings.ENABLE_WEB_SEARCH:
                    web_search_client = WebSearchClient(
                        provider=settings.WEB_SEARCH_PROVIDER,
                        api_key=settings.WEB_SEARCH_API_KEY,
                        base_url=settings.WEB_SEARCH_BASE_URL,
                        timeout=settings.WEB_SEARCH_TIMEOUT,
                    )
                code_exec_client = None
                if settings.ENABLE_CODE_EXEC:
                    code_exec_client = CodeExecClient(
                        timeout_seconds=settings.CODE_EXEC_TIMEOUT_SECONDS,
                        max_output_chars=settings.CODE_EXEC_MAX_OUTPUT_CHARS,
                        max_code_chars=settings.CODE_EXEC_MAX_CODE_CHARS,
                    )

                rag_llm_client = LLMClient(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                    model_name=settings.OPENAI_MODEL_NAME,
                    temperature=0.2,
                    max_tokens=512,
                    timeout=self._request_timeout,
                    usage_callback=usage_callback,
                    usage_label="rag.ask.summary",
                )
                tool_registry = create_tool_registry(
                    rag_client=rag_client,
                    citation_manager=citation_manager_async,
                    web_search_client=web_search_client,
                    code_exec_client=code_exec_client,
                    web_search_max_results=settings.WEB_SEARCH_MAX_RESULTS,
                    paper_search_max_results=settings.PAPER_SEARCH_MAX_RESULTS,
                    rag_llm_client=rag_llm_client,
                )
                tool_router = ToolRouter(tool_registry)
                tool_names = [tool["name"] for tool in tool_registry.list_tools()]
                decision_agent = self._build_decision_agent(
                    language, tool_names, usage_callback=usage_callback
                )
                agent = ResearchAgent(
                    tool_router=tool_router,
                    decision_agent=decision_agent,
                    min_docs_for_compare=settings.MIN_DOCS_FOR_COMPARE,
                    max_docs_for_compare=settings.MAX_DOCS_FOR_COMPARE,
                    followup_mode=settings.FOLLOWUP_EXECUTION_MODE,
                    max_followup_queries=settings.MAX_FOLLOWUP_QUERIES_PER_BLOCK,
                    enable_web_search=settings.ENABLE_WEB_SEARCH,
                    enable_code_exec=settings.ENABLE_CODE_EXEC,
                    max_code_exec_snippets=settings.MAX_CODE_EXEC_SNIPPETS,
                    max_tool_calls=settings.MAX_TOOL_CALLS_PER_BLOCK,
                )
                note_agent = NoteAgent()

                async def run_block(block):
                    async with semaphore:
                        iteration_index = block.iterations + 1
                        if iteration_mode in {"fixed", "flexible"} and block.iterations >= block.max_iterations:
                            await manager_async.mark_status(block.block_id, TopicStatus.FAILED)
                            self._emit_progress(
                                store,
                                queue.research_id,
                                "researching",
                                "Block skipped (max iterations reached)",
                                {
                                    "block_id": block.block_id,
                                    "iterations": block.iterations,
                                    "max_iterations": block.max_iterations,
                                },
                            )
                            return
                        await manager_async.mark_status(block.block_id, TopicStatus.RESEARCHING)
                        progress_payload = {"block_id": block.block_id}
                        if iteration_mode != "legacy":
                            progress_payload["iteration"] = iteration_index
                            progress_payload["max_iterations"] = block.max_iterations
                        self._emit_progress(
                            store,
                            queue.research_id,
                            "researching",
                            f"Researching {block.title}",
                            progress_payload,
                        )
                        try:
                            result = await agent.research_block(
                                block=block,
                                session_id=request.session_id,
                                user_id=user_id,
                                top_k=request.top_k,
                                index_mode=request.index_mode,
                                language=language,
                                context_text=context_text,
                                use_web_search=request.use_web_search,
                                    use_paper_search=getattr(request, "use_paper_search", False),
                                use_code_exec=request.use_code_exec,
                                code_exec_snippets=request.code_exec_snippets,
                            )
                            summary_notes = note_agent.compress(result.summary)
                            block.notes.extend(summary_notes)
                            if summary_notes:
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Summary compressed",
                                    {"block_id": block.block_id, "notes": len(summary_notes)},
                                )
                            for citation in result.citations:
                                block.add_citation(citation.citation_id)
                            if result.main_trace:
                                block.add_trace(result.main_trace)
                            if result.decision:
                                decision_payload = result.decision.to_dict()
                                block.add_decision(decision_payload)
                                tool_calls = [
                                    call.get("name")
                                    for call in decision_payload.get("tool_calls", [])
                                    if call.get("name")
                                ]
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Decision recorded",
                                    {
                                        "block_id": block.block_id,
                                        "sufficient": decision_payload.get("sufficient"),
                                        "should_compare": decision_payload.get("should_compare"),
                                        "followups": len(decision_payload.get("followup_questions", [])),
                                        "tool_calls": tool_calls,
                                        "compare_dimensions": decision_payload.get("compare_dimensions", []),
                                    },
                                )

                            if result.followup_answers:
                                for idx, answer in enumerate(result.followup_answers):
                                    question = (
                                        result.followup_questions[idx]
                                        if idx < len(result.followup_questions)
                                        else "Follow-up question"
                                    )
                                    block.notes.append(f"Follow-up {idx + 1}: {question}")
                                    block.notes.extend(note_agent.compress(answer))
                                for citation in result.followup_citations:
                                    block.add_citation(citation.citation_id)
                                for trace in result.followup_traces:
                                    block.add_trace(trace)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Inline follow-ups executed",
                                    {
                                        "block_id": block.block_id,
                                        "count": len(result.followup_answers),
                                    },
                                )

                            if result.web_search_summary:
                                block.notes.append("Web search highlights:")
                                block.notes.extend(note_agent.compress(result.web_search_summary))
                                for citation in result.web_search_citations:
                                    block.add_citation(citation.citation_id)
                                if result.web_search_trace:
                                    block.add_trace(result.web_search_trace)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Web search completed",
                                    {
                                        "block_id": block.block_id,
                                        "citations": len(result.web_search_citations),
                                    },
                                )

                            if result.paper_search_summary:
                                block.notes.append("Paper search highlights:")
                                block.notes.extend(note_agent.compress(result.paper_search_summary))
                                for citation in result.paper_search_citations:
                                    block.add_citation(citation.citation_id)
                                if result.paper_search_trace:
                                    block.add_trace(result.paper_search_trace)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Paper search completed",
                                    {
                                        "block_id": block.block_id,
                                        "citations": len(result.paper_search_citations),
                                    },
                                )

                            if result.code_exec_outputs:
                                for idx, output in enumerate(result.code_exec_outputs):
                                    block.notes.append(f"Code execution {idx + 1}:")
                                    block.notes.extend(note_agent.compress(output))
                                for trace in result.code_exec_traces:
                                    block.add_trace(trace)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Code execution completed",
                                    {
                                        "block_id": block.block_id,
                                        "snippets": len(result.code_exec_outputs),
                                    },
                                )

                            if result.compare_answer:
                                block.notes.extend(note_agent.compress(result.compare_answer))
                                for citation in result.compare_citations:
                                    block.add_citation(citation.citation_id)
                                if result.compare_trace:
                                    block.add_trace(result.compare_trace)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Compare completed",
                                    {
                                        "block_id": block.block_id,
                                        "citations": len(result.compare_citations),
                                    },
                                )

                            if result.followup_questions and settings.FOLLOWUP_EXECUTION_MODE == "queue":
                                new_blocks = await manager_async.add_followups_from_questions(
                                    block,
                                    result.followup_questions,
                                    language,
                                )
                                if new_blocks:
                                    self._emit_progress(
                                        store,
                                        queue.research_id,
                                        "researching",
                                        f"Decision added {len(new_blocks)} follow-ups for {block.title}",
                                        {"block_id": block.block_id},
                                    )
                            block.iterations = max(block.iterations, iteration_index)
                            block.touch()
                            decision_sufficient = True
                            if result.decision is not None:
                                decision_sufficient = bool(result.decision.sufficient)
                            repeat_reason = None
                            should_repeat = False
                            if iteration_mode == "fixed":
                                should_repeat = block.iterations < block.max_iterations
                                repeat_reason = "fixed"
                            elif iteration_mode == "flexible" and not decision_sufficient:
                                should_repeat = block.iterations < block.max_iterations
                                repeat_reason = "insufficient"

                            if should_repeat:
                                await manager_async.mark_status(block.block_id, TopicStatus.PENDING)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Block queued for next iteration",
                                    {
                                        "block_id": block.block_id,
                                        "iteration": block.iterations,
                                        "max_iterations": block.max_iterations,
                                        "reason": repeat_reason,
                                    },
                                )
                            else:
                                await manager_async.mark_status(block.block_id, TopicStatus.COMPLETED)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Block completed",
                                    {"block_id": block.block_id, "iterations": block.iterations},
                                )

                            new_blocks = await manager_async.maybe_expand(block, result.summary, language)
                            if new_blocks:
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    f"Added {len(new_blocks)} follow-ups for {block.title}",
                                    {"block_id": block.block_id},
                                )
                        except asyncio.CancelledError:
                            await manager_async.mark_status(block.block_id, TopicStatus.PENDING)
                            self._emit_progress(
                                store,
                                queue.research_id,
                                "researching",
                                "Block cancelled",
                                {"block_id": block.block_id},
                            )
                            raise
                        except Exception as exc:  # noqa: BLE001 - capture failures per block
                            block.notes.append(f"Research failed: {exc}")
                            await manager_async.mark_status(block.block_id, TopicStatus.FAILED)
                            self._emit_progress(
                                store,
                                queue.research_id,
                                "researching",
                                "Block failed",
                                {"block_id": block.block_id, "error": str(exc)},
                            )
                        finally:
                            store.save_json("queue.json", queue.to_dict())
                            store.save_json("citations.json", citation_manager.to_dict())

                while True:
                    pending_blocks = [
                        block
                        for block in await manager_async.list_blocks(status=TopicStatus.PENDING)
                        if block.depth > 0
                    ]
                    if not pending_blocks:
                        break
                    await asyncio.gather(*(run_block(block) for block in pending_blocks))
            finally:
                if web_search_client:
                    await web_search_client.close()

        self._emit_progress(
            store,
            queue.research_id,
            "researching",
            "Research completed",
            {"completed": len(queue.list_blocks(status=TopicStatus.COMPLETED))},
        )

    def _emit_progress(
        self,
        store: StateStore,
        research_id: str,
        stage: str,
        message: str,
        payload: Dict[str, Any],
    ) -> None:
        """Append a progress event to the state store.

        Args:
            store (StateStore): State persistence helper.
            research_id (str): Research run id.
            stage (str): Pipeline stage name.
            message (str): Progress message.
            payload (Dict[str, Any]): Additional metadata.
        """

        store.append_progress(
            {
                "research_id": research_id,
                "stage": stage,
                "message": message,
                "timestamp": datetime.utcnow().isoformat(),
                "payload": payload,
            }
        )

    def _build_decision_agent(
        self,
        language: str,
        available_tools: Optional[List[str]],
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> DecisionAgent:
        """Build the decision agent for tool selection."""

        compare_dimensions_en = [
            item.strip()
            for item in settings.COMPARE_DIMENSIONS_EN.split(",")
            if item.strip()
        ]
        compare_dimensions_zh = [
            item.strip()
            for item in settings.COMPARE_DIMENSIONS_ZH.split(",")
            if item.strip()
        ]
        llm_client = LLMClient(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model_name=settings.DECISION_LLM_MODEL_NAME or settings.OPENAI_MODEL_NAME,
            temperature=settings.DECISION_LLM_TEMPERATURE,
            max_tokens=settings.DECISION_LLM_MAX_TOKENS,
            timeout=self._request_timeout,
            usage_callback=usage_callback,
            usage_label="decision_agent",
        )
        return DecisionAgent(
            llm_client=llm_client,
            enabled=settings.DECISION_LLM_ENABLED,
            min_summary_chars=settings.RESEARCH_MIN_SUMMARY_CHARS,
            min_citations=settings.RESEARCH_MIN_CITATIONS,
            max_followups=settings.MAX_FOLLOWUPS_PER_BLOCK,
            compare_dimensions_en=compare_dimensions_en,
            compare_dimensions_zh=compare_dimensions_zh,
            available_tools=available_tools,
        )

    @staticmethod
    def _build_run_summary(queue: DynamicTopicQueue, citation_manager: CitationManager) -> Dict[str, Any]:
        """Build a summary payload for the run."""

        status_counts = {
            TopicStatus.PENDING.value: 0,
            TopicStatus.RESEARCHING.value: 0,
            TopicStatus.COMPLETED.value: 0,
            TopicStatus.FAILED.value: 0,
            TopicStatus.SKIPPED.value: 0,
        }
        tool_counts: Dict[str, int] = {}
        errors: List[Dict[str, Any]] = []
        decision_count = 0
        for block in queue.list_blocks():
            status_counts[block.status.value] = status_counts.get(block.status.value, 0) + 1
            decision_count += len(block.decisions or [])
            for trace in block.tool_traces:
                tool_type = trace.tool_type.value
                tool_counts[tool_type] = tool_counts.get(tool_type, 0) + 1
                if ResearchPipeline._is_trace_error(trace):
                    errors.append(
                        {
                            "block_id": block.block_id,
                            "tool_id": trace.tool_id,
                            "tool_type": tool_type,
                            "summary": trace.summary,
                            "timestamp": trace.timestamp,
                        }
                    )

        return {
            "blocks_total": len(queue.list_blocks()),
            "blocks_by_status": status_counts,
            "citations_total": len(citation_manager.list_citations()),
            "tool_traces_total": sum(tool_counts.values()),
            "tool_traces_by_type": tool_counts,
            "decisions_total": decision_count,
            "errors": errors,
            "generated_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def _is_trace_error(trace: Any) -> bool:
        """Heuristic to detect tool errors."""

        raw = f"{getattr(trace, 'summary', '')} {getattr(trace, 'raw_answer', '')}".lower()
        flags = ["error", "failed", "exception", "错误", "失败", "异常"]
        return any(flag in raw for flag in flags)

    async def _refine_report(
        self,
        *,
        topic: str,
        language: str,
        outline: List[str],
        notes: List[str],
        citation_table: List[str],
        report_style: Optional[str] = None,
        context_text: Optional[str] = None,
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[str]:
        """Optionally refine the report using an LLM.

        Args:
            topic (str): Research topic.
            language (str): Report language code.
            outline (List[str]): Outline lines.
            notes (List[str]): Research notes.
            citation_table (List[str]): Reference table lines.
            context_text (Optional[str]): Optional conversation context.

        Returns:
            Optional[str]: LLM-generated report or None.
        """

        if not getattr(settings, "REPORT_LLM_ENABLED", False):
            return None

        client = LLMClient(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model_name=settings.OPENAI_MODEL_NAME,
            temperature=settings.REPORT_LLM_TEMPERATURE,
            max_tokens=settings.REPORT_LLM_MAX_TOKENS,
            timeout=self._request_timeout,
            usage_callback=usage_callback,
            usage_label="report_refiner",
        )
        refiner = ReportRefiner(client, language=language)
        return await refiner.refine(
            topic=topic,
            outline=outline,
            notes=notes,
            citation_table=citation_table,
            report_style=report_style,
            context_text=context_text,
        )

    async def _refine_report_sectional(
        self,
        *,
        store: StateStore,
        reporter: ReporterAgent,
        research_id: str,
        topic: str,
        language: str,
        outline: List[str],
        report_outline: List[str],
        outline_detailed: List[str],
        notes: List[str],
        citation_table: List[str],
        allowed_refs: List[int],
        draft_report: str,
        report_style: Optional[str] = None,
        context_text: Optional[str] = None,
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[str]:
        """Generate a refined report section-by-section (optional).

        This mode emits progress events per section and updates `report.json` so the
        frontend can preview partial report content while the run is still running.

        Args:
            store (StateStore): State persistence helper.
            reporter (ReporterAgent): Report renderer.
            research_id (str): Research run id.
            topic (str): Research topic.
            language (str): Report language code.
            outline (List[str]): Outline lines.
            report_outline (List[str]): High-level outline items.
            outline_detailed (List[str]): Detailed outline lines.
            notes (List[str]): Research notes.
            citation_table (List[str]): Reference table lines.
            allowed_refs (List[int]): Allowed citation indices.
            draft_report (str): Deterministic draft report.
            report_style (Optional[str]): Report style hint.
            context_text (Optional[str]): Optional conversation context.

        Returns:
            Optional[str]: LLM-generated report or None.
        """

        if not getattr(settings, "REPORT_LLM_ENABLED", False):
            return None

        client = LLMClient(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model_name=settings.OPENAI_MODEL_NAME,
            temperature=settings.REPORT_LLM_TEMPERATURE,
            max_tokens=settings.REPORT_LLM_MAX_TOKENS,
            timeout=self._request_timeout,
            usage_callback=usage_callback,
            usage_label="report_section",
        )
        if not client.is_configured():
            return None

        builder = ReportTemplateBuilder(language=language)
        sections = builder.build_sections()
        total = len(sections)
        if total == 0:
            return None

        title_prefix = "深度研究报告：" if language == "zh" else "DeepResearch Report:"
        header = f"# {title_prefix} {topic}".strip()
        references_section = reporter.render_references_section()
        context_max_chars = int(getattr(settings, "REPORT_LLM_SECTION_CONTEXT_MAX_CHARS", 6000) or 6000)
        section_max_tokens = int(getattr(settings, "REPORT_LLM_SECTION_MAX_TOKENS", 1024) or 1024)

        generated: List[str] = []
        self._emit_progress(
            store,
            research_id,
            "reporting",
            "LLM sectional report generation started",
            {"sections_total": total, "section_max_tokens": section_max_tokens},
        )

        for idx, section in enumerate(sections, start=1):
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "LLM section started",
                {"section_index": idx, "sections_total": total, "section_title": section.title},
            )
            previous_text = "\n\n".join(generated).strip()
            if context_max_chars > 0 and len(previous_text) > context_max_chars:
                previous_text = previous_text[-context_max_chars:]

            prompt = builder.build_section_prompt(
                topic=topic,
                section_title=section.title,
                section_guidance=section.guidance,
                outline=outline,
                notes=notes,
                citation_table=citation_table,
                language=language,
                report_style=report_style,
                previous_text=previous_text or None,
                context_text=context_text,
            )
            raw = await client.generate(prompt, max_tokens=section_max_tokens)
            if not raw:
                return None
            section_markdown = self._normalize_llm_section(raw, expected_title=section.title)
            generated.append(section_markdown)

            report_markdown = "\n\n".join([header, *generated]).strip()
            report_markdown = sanitize_citations(report_markdown, allowed_refs)
            if references_section:
                report_markdown = f"{report_markdown.rstrip()}\n\n{references_section.strip()}\n"

            # Save partial report for snapshot preview.
            store.save_json(
                "report.json",
                {
                    "research_id": research_id,
                    "status": DeepResearchStatus.RUNNING.value,
                    "report_markdown": report_markdown,
                    "report_details": {
                        "outline": report_outline,
                        "outline_detailed": outline_detailed,
                        "notes": notes,
                        "citation_table": citation_table,
                        "draft_markdown": draft_report,
                        "sectional": True,
                        "section_index": idx,
                        "sections_total": total,
                        "current_section": section.title,
                    },
                },
            )

            self._emit_progress(
                store,
                research_id,
                "reporting",
                "LLM section completed",
                {
                    "section_index": idx,
                    "sections_total": total,
                    "section_title": section.title,
                    "chars": len(section_markdown),
                },
            )

        final_report = "\n\n".join([header, *generated]).strip()
        final_report = sanitize_citations(final_report, allowed_refs)
        if references_section:
            final_report = f"{final_report.rstrip()}\n\n{references_section.strip()}\n"
        self._emit_progress(
            store,
            research_id,
            "reporting",
            "LLM sectional report generation completed",
            {"sections_total": total, "chars": len(final_report)},
        )
        return final_report

    @staticmethod
    def _normalize_llm_section(text: str, expected_title: str) -> str:
        """Normalize LLM output to a single markdown section."""

        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
        expected_heading = f"## {expected_title}".strip()
        if not cleaned.startswith("## "):
            cleaned = f"{expected_heading}\n\n{cleaned}".strip()
        if not cleaned.startswith(expected_heading):
            cleaned = f"{expected_heading}\n\n{cleaned}".strip()

        # Keep only until the next level-2 heading to avoid accidental multi-section output.
        marker = "\n## "
        start = cleaned.find(expected_heading)
        if start != -1:
            next_idx = cleaned.find(marker, start + len(expected_heading))
            if next_idx != -1:
                cleaned = cleaned[:next_idx].rstrip()
        return cleaned.strip()

    @staticmethod
    def _plan_item_to_dict(item: PlanItem) -> Dict[str, Any]:
        """Serialize a plan item for persistence.

        Args:
            item (PlanItem): Plan item.

        Returns:
            Dict[str, Any]: Serializable representation.
        """

        return {
            "title": item.title,
            "question": item.question,
            "depth": item.depth,
            "parent_title": item.parent_title,
        }

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
    def _new_research_id() -> str:
        """Create a short research id for storage paths.

        Returns:
            str: Generated research id.
        """

        return f"dr_{uuid.uuid4().hex[:8]}"
