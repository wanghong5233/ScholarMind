"""DeepResearch orchestration pipeline (planning → researching → reporting)."""

from __future__ import annotations

import asyncio
import hashlib
import re
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
from service.llm_client import LLMClient, resolve_llm_config, resolve_llm_endpoints
from service.citation_quality import (
    ACADEMIC_DOMAIN_HINTS,
    DEFAULT_BLOCKED_CONTENT_TERMS,
    DEFAULT_SENSITIVE_DOMAIN_PATTERNS,
    assess_citation_quality,
    matches_domain_pattern,
    normalize_domain,
    split_csv_list,
)
from service.data_structures import DynamicTopicQueue, TopicStatus
from service.code_exec_client import CodeExecClient
from service.rag_client import RAGClient
from service.report_refiner import ReportRefiner
from service.report_quality import analyze_report
from service.report_sanitizer import (
    extract_report_reference_numbers,
    sanitize_report_markdown_structure,
    sanitize_citations,
    strip_placeholder_citation_markers,
    strip_references_section,
)
from service.report_templates import ReportTemplateBuilder
from service.state_store import StateStore
from service.token_usage import TokenUsageTracker
from service.tool_registry import create_tool_registry
from service.tool_router import ToolRouter
from service.web_search_client import WebSearchClient
from utils.language import guess_language
from utils.plan_override import extract_plan_override_items
from core.config import settings


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
        self._validate_fail_fast_request(request)

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
                self._emit_progress(
                    store=store,
                    research_id=research_id,
                    stage="researching",
                    message="Research failed",
                    payload={"error": str(exc)},
                    event_type="run.failed",
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
                self._emit_progress(
                    store,
                    research_id,
                    "planning",
                    "Planning started",
                    {"topic": request.topic},
                )
                plan_items = await self._build_plan(request, user_id)
                if (
                    isinstance(request.metadata, dict)
                    and isinstance(request.metadata.get("plan_override_items"), list)
                ):
                    self._emit_progress(
                        store,
                        research_id,
                        "planning",
                        "Custom plan override applied",
                        {"items": len(plan_items)},
                    )
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
                failed_stage = "researching" if plan_items else "planning"
                failed_message = "Research failed" if plan_items else "Planning failed"
                self._emit_progress(
                    store=store,
                    research_id=research_id,
                    stage=failed_stage,
                    message=failed_message,
                    payload={"error": str(exc)},
                    event_type="run.failed",
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
            selected_citation_ids, citation_filter_stats = self._select_report_citation_ids(
                topic=request.topic,
                citation_manager=citation_manager,
            )
            citation_manager.build_ref_map_for(selected_citation_ids)
            allowed_refs = reporter.allowed_reference_numbers()
            citation_table = reporter.build_citation_table(
                max_items=max(
                    1,
                    int(getattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 80) or 80),
                ),
            )
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Citation quality filter applied",
                {
                    **citation_filter_stats,
                    "citations_after_filter": len(allowed_refs),
                },
            )
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Citation table generated",
                {"citations": len(citation_table)},
            )
            request_provider_override, request_model_override = self._effective_request_llm_overrides(
                request
            )

            if getattr(settings, "REPORT_LLM_SECTIONAL", False):
                llm_report = await self._refine_report_sectional(
                    store=store,
                    reporter=reporter,
                    queue=queue,
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
                    llm_provider_override=request_provider_override,
                    llm_model_override=request_model_override,
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
                    llm_provider_override=request_provider_override,
                    llm_model_override=request_model_override,
                    usage_callback=token_tracker.record,
                )
            if not getattr(settings, "REPORT_LLM_SECTIONAL", False):
                self._emit_progress(
                    store,
                    research_id,
                    "reporting",
                    "LLM refinement completed",
                    {"chars": len(llm_report)},
                )
                report = llm_report
            else:
                report = llm_report
            report, used_refs = self._finalize_report_markdown(
                report_markdown=report,
                reporter=reporter,
                allowed_refs=allowed_refs,
            )
            used_ref_set = set(used_refs)
            final_citations = sorted(
                [
                    citation
                    for citation in citation_manager.list_citations()
                    if citation.ref_number is not None and citation.ref_number in used_ref_set
                ],
                key=lambda citation: citation.ref_number or 0,
            )
            citations_payload = citation_manager.to_dict()
            citations_payload["citations"] = [citation.to_dict() for citation in final_citations]
            citations_payload["ref_number_map"] = {
                citation.citation_id: citation.ref_number
                for citation in final_citations
                if citation.ref_number is not None
            }
            store.save_json("citations.json", citations_payload)

            report_quality = analyze_report(report)
            self._validate_report_quality(
                report_quality=report_quality,
                allowed_refs=allowed_refs,
                completed_blocks=len(queue.list_blocks(status=TopicStatus.COMPLETED)),
            )
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "Report finalized",
                {"citations": len(final_citations)},
            )
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
                    "placeholder_citation_markers": report_quality.get(
                        "placeholder_citation_markers"
                    ),
                    "citations_distinct_count": report_quality.get("citations_distinct_count"),
                    "sections_without_citations": report_quality.get("sections_without_citations") or [],
                },
            )
            summary = self._build_run_summary(queue, citation_manager)
            summary["citations_total"] = len(final_citations)
            report_details = {
                "outline": report_outline,
                "outline_detailed": outline_detailed,
                "notes": report_notes,
                "citation_table": citation_table,
                "draft_markdown": draft_report,
                "quality": report_quality,
                "references_used": used_refs,
                "citations_after_filter": len(final_citations),
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
                {"summary": summary, "final": True},
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

            return DeepResearchResponse(
                research_id=research_id,
                status=DeepResearchStatus.COMPLETED,
                report_markdown=report,
                citations=[self._to_citation_out(c) for c in final_citations],
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
        override_items = extract_plan_override_items(
            request.metadata,
            max_depth=request.depth,
            max_breadth=request.breadth,
        )
        if override_items:
            return override_items

        planner = PlannerAgent(
            depth=request.depth,
            breadth=request.breadth,
            language=request.language,
        )
        if not request.session_id:
            raise ValueError("DeepResearch planning requires session_id")

        plan_endpoints = self._resolve_llm_endpoints_for_request(request)
        last_exc: Optional[Exception] = None
        async with RAGClient(self._rag_service_url, timeout=self._request_timeout) as rag_client:
            for endpoint in plan_endpoints:
                try:
                    return await planner.plan_with_rag(
                        topic=request.topic,
                        rag_client=rag_client,
                        session_id=request.session_id,
                        user_id=user_id,
                        top_k=request.top_k,
                        index_mode=request.index_mode,
                        llm_provider=endpoint.provider,
                        llm_model=endpoint.model_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    continue
        if last_exc is not None:
            raise RuntimeError(f"LLM planning failed: {str(last_exc)}") from last_exc
        raise RuntimeError("LLM planning failed: no endpoint candidates available.")

    @staticmethod
    def _effective_request_llm_overrides(
        request: DeepResearchRequest,
    ) -> tuple[Optional[str], Optional[str]]:
        """Return request-level provider/model overrides when explicitly allowed."""

        if not getattr(settings, "DEEP_RESEARCH_ALLOW_REQUEST_LLM_OVERRIDE", False):
            return None, None
        provider = str(request.llm_provider or "").strip().lower() or None
        model_name = str(request.llm_model or "").strip() or None
        return provider, model_name

    def _resolve_llm_endpoints_for_request(
        self,
        request: DeepResearchRequest,
        *,
        model_name_override: Optional[str] = None,
    ):
        """Resolve runtime LLM endpoint chain for this run."""

        provider_override, request_model_override = self._effective_request_llm_overrides(request)
        model_override = model_name_override or request_model_override
        return resolve_llm_endpoints(
            provider_override=provider_override,
            model_name_override=model_override,
            allow_request_override=True,
        )

    def _validate_fail_fast_request(self, request: DeepResearchRequest) -> None:
        """Validate request/runtime prerequisites in strict fail-fast mode."""

        if not getattr(settings, "STRICT_FAIL_FAST", False):
            return

        if not request.session_id:
            raise ValueError("DeepResearch requires session_id in STRICT_FAIL_FAST mode.")

        if not getattr(settings, "REPORT_LLM_ENABLED", False):
            raise RuntimeError(
                "REPORT_LLM_ENABLED=false is not allowed in STRICT_FAIL_FAST mode."
            )
        if not getattr(settings, "REPORT_LLM_SECTIONAL", False):
            raise RuntimeError(
                "REPORT_LLM_SECTIONAL=false is not allowed in STRICT_FAIL_FAST mode."
            )
        if not getattr(settings, "DECISION_LLM_ENABLED", False):
            raise RuntimeError(
                "DECISION_LLM_ENABLED=false is not allowed in STRICT_FAIL_FAST mode."
            )
        if request.use_web_search:
            if not settings.ENABLE_WEB_SEARCH:
                raise RuntimeError(
                    "Request asks for web search but ENABLE_WEB_SEARCH=false in STRICT_FAIL_FAST mode."
                )
            api_key = self._resolve_web_search_api_key(settings.WEB_SEARCH_PROVIDER)
            if not api_key:
                raise RuntimeError(
                    "Web search is requested but WEB_SEARCH_API_KEY/TAVILY_API_KEY/SERPER_API_KEY is missing."
                )
        if request.use_code_exec and not settings.ENABLE_CODE_EXEC:
            raise RuntimeError(
                "Request asks for code execution but ENABLE_CODE_EXEC=false in STRICT_FAIL_FAST mode."
            )
        if request.use_paper_search:
            providers = [
                item.strip()
                for item in str(settings.PAPER_SEARCH_PROVIDERS or "").split(",")
                if item.strip()
            ]
            if not providers:
                raise RuntimeError(
                    "paper.search is requested but PAPER_SEARCH_PROVIDERS is empty."
                )

        # Validate strict LLM config early (planner/decision/report all depend on it).
        provider_override, model_override = self._effective_request_llm_overrides(request)
        resolve_llm_config(
            provider_override=provider_override,
            model_name_override=model_override,
            allow_request_override=True,
        )

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
            raise ValueError("DeepResearch context requires session_id")
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
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch DeepResearch context pack: {exc}") from exc

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
            raise RuntimeError("DeepResearch requires session_id for research execution.")

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
                    web_search_api_key = self._resolve_web_search_api_key(
                        provider=settings.WEB_SEARCH_PROVIDER,
                    )
                    if not web_search_api_key:
                        raise RuntimeError(
                            "ENABLE_WEB_SEARCH=true but WEB_SEARCH_API_KEY/TAVILY_API_KEY/SERPER_API_KEY is missing."
                        )
                    web_search_client = WebSearchClient(
                        provider=settings.WEB_SEARCH_PROVIDER,
                        api_key=web_search_api_key,
                        base_url=settings.WEB_SEARCH_BASE_URL,
                        timeout=settings.WEB_SEARCH_TIMEOUT,
                    )
                elif request.use_web_search:
                    raise RuntimeError(
                        "Request asks for web search but ENABLE_WEB_SEARCH=false in deep-research config."
                    )
                code_exec_client = None
                if settings.ENABLE_CODE_EXEC:
                    code_exec_client = CodeExecClient(
                        timeout_seconds=settings.CODE_EXEC_TIMEOUT_SECONDS,
                        max_output_chars=settings.CODE_EXEC_MAX_OUTPUT_CHARS,
                        max_code_chars=settings.CODE_EXEC_MAX_CODE_CHARS,
                    )
                elif request.use_code_exec:
                    raise RuntimeError(
                        "Request asks for code execution but ENABLE_CODE_EXEC=false in deep-research config."
                    )

                rag_endpoints = self._resolve_llm_endpoints_for_request(request)
                rag_primary = rag_endpoints[0]
                rag_llm_client = LLMClient(
                    api_key=rag_primary.api_key,
                    base_url=rag_primary.base_url,
                    model_name=rag_primary.model_name,
                    temperature=0.2,
                    max_tokens=512,
                    timeout=self._request_timeout,
                    usage_callback=usage_callback,
                    usage_label="rag.ask.summary",
                    endpoint_chain=rag_endpoints,
                    provider=rag_primary.provider,
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
                tool_router = ToolRouter(
                    tool_registry,
                    observer=lambda event: self._emit_tool_event(
                        store=store,
                        research_id=queue.research_id,
                        event=event,
                    ),
                )
                tool_names = [tool["name"] for tool in tool_registry.list_tools()]
                web_tool_available = "web.search" in tool_names
                paper_tool_available = "paper.search" in tool_names
                code_tool_available = "code.exec" in tool_names
                if request.use_web_search and not web_tool_available:
                    raise RuntimeError(
                        "Web search requested but tool is unavailable (provider/config mismatch)."
                    )
                if getattr(request, "use_paper_search", False) and not paper_tool_available:
                    raise RuntimeError("Paper search requested but paper.search tool is unavailable.")
                if request.use_code_exec and not code_tool_available:
                    raise RuntimeError(
                        "Code execution requested but code.exec tool is unavailable."
                    )
                decision_agent = self._build_decision_agent(
                    language,
                    tool_names,
                    request=request,
                    usage_callback=usage_callback,
                )
                agent = ResearchAgent(
                    tool_router=tool_router,
                    decision_agent=decision_agent,
                    min_docs_for_compare=settings.MIN_DOCS_FOR_COMPARE,
                    max_docs_for_compare=settings.MAX_DOCS_FOR_COMPARE,
                    followup_mode=settings.FOLLOWUP_EXECUTION_MODE,
                    max_followup_queries=settings.MAX_FOLLOWUP_QUERIES_PER_BLOCK,
                    enable_web_search=web_tool_available,
                    enable_code_exec=code_tool_available,
                    max_code_exec_snippets=settings.MAX_CODE_EXEC_SNIPPETS,
                    max_tool_calls=settings.MAX_TOOL_CALLS_PER_BLOCK,
                    max_decision_rounds=settings.AGENT_DECISION_MAX_ROUNDS,
                    min_evidence_quality_score=settings.AGENT_MIN_EVIDENCE_QUALITY_SCORE,
                    fail_fast_on_tool_error=settings.AGENT_FAIL_FAST_ON_TOOL_ERROR,
                    allow_followup_query_expansion=settings.AGENT_USE_FOLLOWUP_AS_SEARCH_QUERY,
                    action_beam_width=max(
                        1,
                        int(getattr(settings, "AGENT_ACTION_BEAM_WIDTH", 3) or 3),
                    ),
                    enable_code_exec_auto=bool(
                        getattr(settings, "AGENT_ENABLE_CODE_EXEC_AUTO", True)
                    ),
                    academic_paper_first=bool(
                        getattr(settings, "AGENT_ACADEMIC_PAPER_FIRST", True)
                    ),
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
                                    "block_title": block.title,
                                    "iterations": block.iterations,
                                    "max_iterations": block.max_iterations,
                                },
                            )
                            return
                        await manager_async.mark_status(block.block_id, TopicStatus.RESEARCHING)
                        progress_payload = {
                            "block_id": block.block_id,
                            "block_title": block.title,
                        }
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
                                global_topic=request.topic,
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
                                    {
                                        "block_id": block.block_id,
                                        "block_title": block.title,
                                        "notes": len(summary_notes),
                                    },
                                )
                            for citation in result.citations:
                                block.add_citation(citation.citation_id)
                            if result.main_trace:
                                block.add_trace(result.main_trace)
                            decision_records = list(result.decision_history or [])
                            if not decision_records and result.decision:
                                decision_records = [result.decision.to_dict()]
                            if decision_records:
                                for decision_record in decision_records:
                                    block.add_decision(decision_record)
                                decision_payload = decision_records[-1]
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
                                        "block_title": block.title,
                                        "sufficient": decision_payload.get("sufficient"),
                                        "should_compare": decision_payload.get("should_compare"),
                                        "followups": len(decision_payload.get("followup_questions", [])),
                                        "tool_calls": tool_calls,
                                        "compare_dimensions": decision_payload.get("compare_dimensions", []),
                                        "decision_rounds": len(decision_records),
                                        "quality_score": result.evidence_quality_score,
                                        "rationale_preview": str(
                                            decision_payload.get("rationale") or ""
                                        )[:240],
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
                                        "block_title": block.title,
                                        "count": len(result.followup_answers),
                                    },
                                )

                            if result.web_search_summary:
                                block.notes.append("Web search highlights:")
                                block.notes.extend(note_agent.compress(result.web_search_summary))
                                for citation in result.web_search_citations:
                                    block.add_citation(citation.citation_id)
                                web_traces = result.web_search_traces or (
                                    [result.web_search_trace] if result.web_search_trace else []
                                )
                                for trace in web_traces:
                                    block.add_trace(trace)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Web search completed",
                                    {
                                        "block_id": block.block_id,
                                        "block_title": block.title,
                                        "citations_found": len(result.web_search_citations),
                                        "traces": len(web_traces),
                                        "quality_score": self._score_evidence_quality(
                                            summary=result.web_search_summary,
                                            citations_count=len(result.web_search_citations),
                                            traces_count=len(web_traces),
                                        ),
                                    },
                                )

                            if result.paper_search_summary:
                                block.notes.append("Paper search highlights:")
                                block.notes.extend(note_agent.compress(result.paper_search_summary))
                                for citation in result.paper_search_citations:
                                    block.add_citation(citation.citation_id)
                                paper_traces = result.paper_search_traces or (
                                    [result.paper_search_trace] if result.paper_search_trace else []
                                )
                                for trace in paper_traces:
                                    block.add_trace(trace)
                                self._emit_progress(
                                    store,
                                    queue.research_id,
                                    "researching",
                                    "Paper search completed",
                                    {
                                        "block_id": block.block_id,
                                        "block_title": block.title,
                                        "citations_found": len(result.paper_search_citations),
                                        "traces": len(paper_traces),
                                        "quality_score": self._score_evidence_quality(
                                            summary=result.paper_search_summary,
                                            citations_count=len(result.paper_search_citations),
                                            traces_count=len(paper_traces),
                                        ),
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
                                        "block_title": block.title,
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
                                        "block_title": block.title,
                                        "citations_found": len(result.compare_citations),
                                    },
                                )

                            if result.followup_questions:
                                merged_followups = self._merge_followup_questions_for_plan(
                                    result.followup_questions,
                                    language=language,
                                    max_items=max(
                                        1,
                                        int(
                                            getattr(
                                                settings,
                                                "FOLLOWUP_PLAN_MAX_ITEMS",
                                                settings.MAX_FOLLOWUPS_PER_BLOCK,
                                            )
                                            or settings.MAX_FOLLOWUPS_PER_BLOCK
                                        ),
                                    ),
                                )
                                if merged_followups:
                                    heading = (
                                        "Deferred follow-up plan:"
                                        if language != "zh"
                                        else "待执行后续研究计划："
                                    )
                                    block.notes.append(heading)
                                    block.notes.extend([f"- {item}" for item in merged_followups])
                                    self._emit_progress(
                                        store,
                                        queue.research_id,
                                        "researching",
                                        "Follow-ups merged into current plan",
                                        {
                                            "block_id": block.block_id,
                                            "block_title": block.title,
                                            "count": len(merged_followups),
                                        },
                                    )

                                if (
                                    settings.FOLLOWUP_EXECUTION_MODE == "queue"
                                    and bool(
                                        getattr(settings, "FOLLOWUP_QUEUE_EXPANSION_ENABLED", False)
                                    )
                                ):
                                    new_blocks = await manager_async.add_followups_from_questions(
                                        block,
                                        merged_followups or result.followup_questions,
                                        language,
                                    )
                                    if new_blocks:
                                        self._emit_progress(
                                            store,
                                            queue.research_id,
                                            "researching",
                                            f"Decision added {len(new_blocks)} follow-ups for {block.title}",
                                            {
                                                "block_id": block.block_id,
                                                "block_title": block.title,
                                            },
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
                                        "block_title": block.title,
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
                                    {
                                        "block_id": block.block_id,
                                        "block_title": block.title,
                                        "iterations": block.iterations,
                                    },
                                )

                            if bool(getattr(settings, "AUTO_EXPAND_FROM_SUMMARY", False)):
                                new_blocks = await manager_async.maybe_expand(
                                    block, result.summary, language
                                )
                                if new_blocks:
                                    self._emit_progress(
                                        store,
                                        queue.research_id,
                                        "researching",
                                        f"Added {len(new_blocks)} follow-ups for {block.title}",
                                        {
                                            "block_id": block.block_id,
                                            "block_title": block.title,
                                        },
                                    )
                        except asyncio.CancelledError:
                            await manager_async.mark_status(block.block_id, TopicStatus.PENDING)
                            self._emit_progress(
                                store,
                                queue.research_id,
                                "researching",
                                "Block cancelled",
                                {
                                    "block_id": block.block_id,
                                    "block_title": block.title,
                                },
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
                                {
                                    "block_id": block.block_id,
                                    "block_title": block.title,
                                    "error": str(exc),
                                },
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
        event_type: Optional[str] = None,
    ) -> None:
        """Append a progress event to the state store.

        Args:
            store (StateStore): State persistence helper.
            research_id (str): Research run id.
            stage (str): Pipeline stage name.
            message (str): Progress message.
            payload (Dict[str, Any]): Additional metadata.
            event_type (Optional[str]): Structured event type for UI rendering.
        """

        resolved_event_type = event_type or self._resolve_event_type(stage, message)
        store.append_progress(
            {
                "research_id": research_id,
                "stage": stage,
                "message": message,
                "timestamp": datetime.utcnow().isoformat(),
                "payload": payload,
                "event_type": resolved_event_type,
            }
        )

    def _emit_tool_event(
        self,
        store: StateStore,
        research_id: str,
        event: Dict[str, Any],
    ) -> None:
        """Convert tool lifecycle callbacks into structured progress events."""

        event_type = str(event.get("event_type") or "").strip() or "tool.progress"
        tool_name = str(event.get("tool") or "").strip()
        block_id = str(event.get("block_id") or "").strip()
        block_title = str(event.get("block_title") or "").strip()
        query_preview = self._extract_tool_query(event.get("parameters") or {})
        payload = {
            "block_id": block_id,
            "block_title": block_title or None,
            "tool": tool_name,
            "tool_type": event.get("tool_type") or self._map_tool_name_to_type(tool_name),
            "tool_id": event.get("tool_id"),
            "purpose": event.get("purpose"),
            "query": query_preview or event.get("query"),
            "success": event.get("success"),
            "error": event.get("error"),
            "summary_preview": str(event.get("summary") or "").strip()[:280]
            if event.get("summary")
            else None,
        }
        payload = {key: value for key, value in payload.items() if value not in (None, "", [])}

        label = self._tool_name_to_label(tool_name)
        if event_type == "tool.started":
            message = f"Tool started: {label}"
        elif event_type == "tool.failed":
            message = f"Tool failed: {label}"
        else:
            message = f"Tool completed: {label}"

        self._emit_progress(
            store=store,
            research_id=research_id,
            stage="researching",
            message=message,
            payload=payload,
            event_type=event_type,
        )

    @staticmethod
    def _score_evidence_quality(
        *,
        summary: Optional[str],
        citations_count: int,
        traces_count: int,
    ) -> int:
        """Return a lightweight evidence quality score (0-100)."""

        summary_chars = len(str(summary or "").strip())
        summary_component = min(1.0, summary_chars / 800.0) * 40.0
        citation_component = min(1.0, max(0, citations_count) / 6.0) * 35.0
        trace_component = min(1.0, max(0, traces_count) / 6.0) * 25.0
        return int(round(summary_component + citation_component + trace_component))

    @staticmethod
    def _validate_report_quality(
        *,
        report_quality: Dict[str, Any],
        allowed_refs: List[int],
        completed_blocks: int,
    ) -> None:
        """Validate report quality before persisting a completed run."""

        min_completed_blocks = max(
            1,
            int(getattr(settings, "REPORT_MIN_COMPLETED_BLOCKS", 1) or 1),
        )
        if completed_blocks < min_completed_blocks:
            raise RuntimeError(
                "LLM report quality gate failed: "
                f"completed_blocks={completed_blocks} < {min_completed_blocks}."
            )

        min_paragraphs = max(
            1,
            int(getattr(settings, "REPORT_MIN_PARAGRAPHS_TOTAL", 6) or 6),
        )
        paragraphs_total = int(report_quality.get("paragraphs_total") or 0)
        if paragraphs_total < min_paragraphs:
            raise RuntimeError(
                "LLM report quality gate failed: "
                f"paragraphs_total={paragraphs_total} < {min_paragraphs}."
            )

        min_distinct_cfg = max(
            1,
            int(getattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 2) or 2),
        )
        min_distinct_required = min_distinct_cfg
        if len(allowed_refs) < min_distinct_required:
            raise RuntimeError(
                "LLM report quality gate failed: "
                f"high_quality_refs={len(allowed_refs)} < {min_distinct_required}."
            )

        # NOTE: placeholder_citation_markers are stripped in _finalize_report_markdown
        # before this check runs, so the value here is always 0 for valid runs.
        # We keep the check for observability / defensive programming only.
        placeholder_markers = int(report_quality.get("placeholder_citation_markers") or 0)
        if placeholder_markers > 0:
            # Should never happen after stripping, but log it as observability data.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Placeholder citation markers still present after sanitization: %d", placeholder_markers
            )

        citations_mentions = int(report_quality.get("citations_mentions") or 0)
        if citations_mentions <= 0:
            raise RuntimeError(
                "LLM report quality gate failed: no clickable citation markers detected."
            )
        citations_distinct_count = int(report_quality.get("citations_distinct_count") or 0)
        if citations_distinct_count < min_distinct_required:
            raise RuntimeError(
                "LLM report quality gate failed: "
                f"citations_distinct_count={citations_distinct_count} < {min_distinct_required}."
            )

        min_coverage = float(
            getattr(settings, "REPORT_MIN_CITATION_PARAGRAPH_COVERAGE", 0.2) or 0.2
        )
        coverage = float(report_quality.get("citation_paragraph_coverage") or 0.0)
        if coverage < min_coverage:
            raise RuntimeError(
                "LLM report quality gate failed: "
                f"citation_paragraph_coverage={coverage:.3f} < {min_coverage:.3f}."
            )

    @staticmethod
    def _is_retrieval_disabled(index_mode: Optional[str]) -> bool:
        """Return True when request explicitly disables KB retrieval."""

        normalized = str(index_mode or "").strip().lower()
        return normalized in {"disabled", "off", "none", "false", "0"}

    @staticmethod
    def _extract_tool_query(parameters: Dict[str, Any]) -> str:
        """Extract a readable query from tool parameters."""

        if not isinstance(parameters, dict):
            return ""
        for key in ("query", "question", "url", "code"):
            raw = parameters.get(key)
            if isinstance(raw, str) and raw.strip():
                text = raw.strip().replace("\n", " ")
                return text[:220]
        return ""

    @staticmethod
    def _resolve_web_search_api_key(provider: str) -> Optional[str]:
        """Resolve web search key from canonical/compatible env names."""

        direct = str(settings.WEB_SEARCH_API_KEY or "").strip()
        if direct:
            return direct
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider == "tavily":
            alias_key = str(settings.TAVILY_API_KEY or "").strip()
            return alias_key or None
        if normalized_provider == "serper":
            alias_key = str(settings.SERPER_API_KEY or "").strip()
            return alias_key or None
        return None

    @staticmethod
    def _map_tool_name_to_type(tool_name: str) -> str:
        """Map tool names to a normalized tool type."""

        normalized = (tool_name or "").strip().lower()
        mapping = {
            "rag.ask": "rag",
            "rag.compare": "compare",
            "web.search": "search",
            "web.open_page": "search",
            "web.find_in_page": "search",
            "paper.search": "search",
            "code.exec": "code",
        }
        return mapping.get(normalized, "")

    @staticmethod
    def _tool_name_to_label(tool_name: str) -> str:
        """Convert tool ids into user-friendly labels."""

        normalized = (tool_name or "").strip().lower()
        labels = {
            "rag.ask": "RAG Ask",
            "rag.compare": "RAG Compare",
            "web.search": "Web Search",
            "web.open_page": "Web Open Page",
            "web.find_in_page": "Web Find In Page",
            "paper.search": "Paper Search",
            "code.exec": "Code Exec",
        }
        return labels.get(normalized, tool_name or "tool")

    @staticmethod
    def _resolve_event_type(stage: str, message: str) -> str:
        """Infer a structured event type from stage and message."""

        msg = (message or "").strip().lower()
        if msg.startswith("tool started:"):
            return "tool.started"
        if msg.startswith("tool failed:"):
            return "tool.failed"
        if msg.startswith("tool completed:"):
            return "tool.completed"
        if "decision recorded" in msg:
            return "decision.recorded"
        if "follow-up" in msg or "followup" in msg:
            return "followup.progress"
        if "reporting completed" in msg:
            return "report.completed"
        if stage == "planning":
            if "completed" in msg:
                return "plan.completed"
            if "started" in msg:
                return "plan.started"
            return "plan.progress"
        if stage == "researching":
            if "started" in msg:
                return "research.started"
            if "completed" in msg:
                return "research.completed"
            if "failed" in msg:
                return "research.failed"
            return "research.progress"
        if stage == "reporting":
            if "started" in msg:
                return "report.started"
            if "failed" in msg:
                return "report.failed"
            return "report.progress"
        if stage == "control":
            return "control.event"
        return "progress.update"

    def _build_decision_agent(
        self,
        language: str,
        available_tools: Optional[List[str]],
        request: DeepResearchRequest,
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
        request_provider_override, request_model_override = self._effective_request_llm_overrides(
            request
        )
        decision_model_override = settings.DECISION_LLM_MODEL_NAME or request_model_override or None
        decision_endpoints = resolve_llm_endpoints(
            provider_override=request_provider_override,
            model_name_override=decision_model_override,
            allow_request_override=True,
        )
        decision_primary = decision_endpoints[0]
        llm_client = LLMClient(
            api_key=decision_primary.api_key,
            base_url=decision_primary.base_url,
            model_name=decision_primary.model_name,
            temperature=settings.DECISION_LLM_TEMPERATURE,
            max_tokens=settings.DECISION_LLM_MAX_TOKENS,
            timeout=self._request_timeout,
            usage_callback=usage_callback,
            usage_label="decision_agent",
            endpoint_chain=decision_endpoints,
            provider=decision_primary.provider,
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
    def _domain_patterns_for_report_filter() -> tuple[List[str], List[str]]:
        """Resolve report-citation allow/deny lists from config."""

        allowlist = split_csv_list(getattr(settings, "REPORT_CITATION_DOMAIN_ALLOWLIST", ""))
        denylist = split_csv_list(getattr(settings, "REPORT_CITATION_DOMAIN_DENYLIST", ""))
        if not denylist:
            denylist = list(DEFAULT_SENSITIVE_DOMAIN_PATTERNS)
        return allowlist, denylist

    @staticmethod
    def _blocked_terms_for_report_filter() -> List[str]:
        """Resolve blocked lexical terms for report citations."""

        blocked = split_csv_list(getattr(settings, "REPORT_CITATION_BLOCKED_TERMS", ""))
        if not blocked:
            blocked = list(DEFAULT_BLOCKED_CONTENT_TERMS)
        return blocked

    @staticmethod
    def _source_priority(source_type: str) -> int:
        """Return source-priority weight for ranking final references."""

        normalized = str(source_type or "").strip().lower()
        if "paper" in normalized:
            return 3
        if "rag" in normalized:
            return 2
        return 1

    def _select_report_citation_ids(
        self,
        *,
        topic: str,
        citation_manager: CitationManager,
    ) -> tuple[List[str], Dict[str, Any]]:
        """Select high-quality citations for final report grounding."""

        citations = list(citation_manager.list_citations() or [])
        max_total = max(1, int(getattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 80) or 80))
        min_distinct_target = max(
            1,
            min(
                max_total,
                int(getattr(settings, "REPORT_MIN_DISTINCT_CITATIONS", 2) or 2),
            ),
        )
        min_score = float(getattr(settings, "REPORT_CITATION_MIN_QUALITY_SCORE", 0.9) or 0.9)
        min_overlap = float(getattr(settings, "REPORT_CITATION_MIN_QUERY_OVERLAP", 0.12) or 0.12)
        web_min_overlap = float(
            getattr(settings, "REPORT_CITATION_WEB_MIN_QUERY_OVERLAP", 0.18) or 0.18
        )
        relaxed_min_overlap = float(
            getattr(settings, "REPORT_CITATION_RELAXED_MIN_QUERY_OVERLAP", 0.08) or 0.08
        )
        relaxed_min_score = float(
            getattr(settings, "REPORT_CITATION_RELAXED_MIN_QUALITY_SCORE", 0.55) or 0.55
        )
        allowlist, denylist = self._domain_patterns_for_report_filter()
        blocked_terms = self._blocked_terms_for_report_filter()

        accepted: List[tuple[int, float, float, int, Any]] = []
        relaxed_accepted: List[tuple[int, float, float, int, Any]] = []
        dropped_reasons: Dict[str, int] = {}
        seen_content_keys: set[str] = set()

        for index, citation in enumerate(citations):
            content_key = "|".join(
                [
                    str(citation.url or "").strip().lower(),
                    str(citation.title or "").strip().lower(),
                ]
            )
            if content_key in seen_content_keys:
                dropped_reasons["duplicate_url_or_title"] = (
                    dropped_reasons.get("duplicate_url_or_title", 0) + 1
                )
                continue
            seen_content_keys.add(content_key)

            source_type = str(citation.source_type or "")
            normalized_source = source_type.strip().lower()

            # Trusted academic domains (arxiv, semanticscholar, ieee, etc.) are already
            # pre-vetted by source type; skip the topic-overlap check entirely so that
            # edge-computing papers written in English but retrieved via Chinese queries
            # are not spuriously rejected because their titles lack the CJK tokens.
            citation_domain = normalize_domain(str(citation.url or ""))
            is_trusted_academic = (
                matches_domain_pattern(citation_domain, ACADEMIC_DOMAIN_HINTS)
                or "paper" in normalized_source
            )
            if is_trusted_academic:
                # Domain authority + source boost already guarantee score >> min_score;
                # only domain/blocked-term guards apply, topic overlap is waived.
                source_min_overlap = 0.0
            elif "web" in normalized_source:
                source_min_overlap = max(min_overlap, web_min_overlap)
            else:
                source_min_overlap = min_overlap

            assessment = assess_citation_quality(
                topic=topic,
                title=str(citation.title or ""),
                snippet=str(citation.snippet or ""),
                url=str(citation.url or ""),
                source_type=source_type,
                allowlist=allowlist,
                denylist=denylist,
                blocked_terms=blocked_terms,
                min_score=min_score,
                min_query_overlap=source_min_overlap,
            )
            if not assessment.accepted:
                dropped_reasons[assessment.reason or "rejected"] = (
                    dropped_reasons.get(assessment.reason or "rejected", 0) + 1
                )

                # For trusted academic sources that failed score (shouldn't normally happen),
                # also try the relaxed filter with overlap waived.
                relaxed_source_overlap = 0.0 if is_trusted_academic else relaxed_min_overlap
                if not is_trusted_academic and "web" in normalized_source:
                    relaxed_source_overlap = max(
                        relaxed_min_overlap,
                        min(web_min_overlap, max(relaxed_min_overlap, web_min_overlap * 0.75)),
                    )
                relaxed_assessment = assess_citation_quality(
                    topic=topic,
                    title=str(citation.title or ""),
                    snippet=str(citation.snippet or ""),
                    url=str(citation.url or ""),
                    source_type=source_type,
                    allowlist=allowlist,
                    denylist=denylist,
                    blocked_terms=blocked_terms,
                    min_score=relaxed_min_score,
                    min_query_overlap=relaxed_source_overlap,
                )
                if relaxed_assessment.accepted:
                    source_priority = self._source_priority(source_type)
                    relaxed_accepted.append(
                        (
                            source_priority,
                            relaxed_assessment.overlap,
                            relaxed_assessment.score,
                            -index,
                            citation,
                        )
                    )
                continue

            source_priority = self._source_priority(source_type)
            accepted.append(
                (
                    source_priority,
                    assessment.overlap,
                    assessment.score,
                    -index,
                    citation,
                )
            )

        accepted.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
        relaxed_accepted.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)

        # Start with strictly accepted pool; then ALWAYS supplement from relaxed pool
        # up to max_total – not just to min_distinct_target.  Previously the relaxed fill
        # stopped at min_distinct_target=2, so if the strict pool already had ≥2 entries,
        # all the relaxed-tier papers (still relevant but slightly lower score) were silently
        # discarded, resulting in sparse final reference lists.
        selection_pool = list(accepted)
        filter_mode = "strict" if accepted else "empty"
        if relaxed_accepted:
            selected_ids = {item[4].citation_id for item in selection_pool}
            for relaxed_item in relaxed_accepted:
                if len(selection_pool) >= max_total:
                    break
                cit = relaxed_item[4]
                if cit.citation_id in selected_ids:
                    continue
                selection_pool.append(relaxed_item)
                selected_ids.add(cit.citation_id)
            if len(selection_pool) > len(accepted):
                filter_mode = "strict_with_relaxed_fill" if accepted else "relaxed_fallback"

        selection_pool.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
        selected_citation_ids = [item[4].citation_id for item in selection_pool[:max_total]]
        stats: Dict[str, Any] = {
            "citations_total_before_filter": len(citations),
            "citations_total_after_filter": len(selected_citation_ids),
            "citations_dropped": max(0, len(citations) - len(selected_citation_ids)),
            "citation_drop_reasons": dropped_reasons,
            "citation_filter_mode": filter_mode,
            "citation_filter_min_score": min_score,
            "citation_filter_min_overlap": min_overlap,
            "citation_filter_web_min_overlap": web_min_overlap,
            "citation_filter_relaxed_min_score": relaxed_min_score,
            "citation_filter_relaxed_min_overlap": relaxed_min_overlap,
            "citation_filter_min_distinct_target": min_distinct_target,
        }
        return selected_citation_ids, stats

    @staticmethod
    def _cap_section_reference_mentions(report_body: str, *, max_refs_per_section: int) -> str:
        """Cap distinct reference mentions per section."""

        cap = max(1, int(max_refs_per_section or 1))
        pattern = re.compile(r"\[\[(\d+)\]\]\(#ref-\1\)")
        lines = (report_body or "").splitlines()

        sections: List[List[str]] = []
        current: List[str] = []
        for line in lines:
            if line.startswith("## ") and current:
                sections.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append(current)

        def _limit_chunk(chunk: str) -> str:
            seen: set[int] = set()

            def _replace(match: re.Match[str]) -> str:
                ref = int(match.group(1))
                if ref in seen:
                    return match.group(0)
                if len(seen) >= cap:
                    return ""
                seen.add(ref)
                return match.group(0)

            return pattern.sub(_replace, chunk)

        limited_chunks = [_limit_chunk("\n".join(chunk)) for chunk in sections]
        return "\n".join(limited_chunks).strip()

    def _finalize_report_markdown(
        self,
        *,
        report_markdown: str,
        reporter: ReporterAgent,
        allowed_refs: List[int],
    ) -> tuple[str, List[int]]:
        """Sanitize citations, enforce caps, and rebuild references section."""

        report_body = strip_references_section(report_markdown or "")
        # Strip [N]/[n]/[?] placeholder markers before quality checks so that a
        # report with real clickable citations AND a few stray placeholders is not
        # unnecessarily failed by the quality gate.  Only "no real citations at all"
        # should trigger a failure.
        report_body = strip_placeholder_citation_markers(report_body)
        report_body = sanitize_report_markdown_structure(report_body)
        report_body = sanitize_citations(report_body, allowed_refs)
        report_body = self._cap_section_reference_mentions(
            report_body,
            max_refs_per_section=int(getattr(settings, "REPORT_REFERENCES_MAX_PER_SECTION", 12) or 12),
        )

        used_refs = extract_report_reference_numbers(report_body, allowed_refs=allowed_refs)
        max_total = max(1, int(getattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 80) or 80))
        if len(used_refs) > max_total:
            used_refs = used_refs[:max_total]
            report_body = sanitize_citations(report_body, used_refs)

        references_section = reporter.render_references_section(
            only_refs=used_refs,
            max_items=max_total,
        )
        finalized = report_body.strip()
        if references_section:
            finalized = f"{finalized.rstrip()}\n\n{references_section.strip()}\n"
        return finalized, used_refs

    @staticmethod
    def _looks_like_user_clarification_followup(question: str) -> bool:
        """Detect user-facing clarification prompts that should not become new blocks."""

        normalized = " ".join(str(question or "").strip().lower().split())
        if not normalized:
            return True
        zh_markers = (
            "你希望",
            "你更想",
            "你更倾向",
            "请提供",
            "请给出",
            "你能否",
            "是否需要",
            "请在",
        )
        en_markers = (
            "can you provide",
            "could you provide",
            "would you like",
            "do you prefer",
            "what is your preference",
            "please provide",
            "please share",
        )
        if any(marker in normalized for marker in zh_markers):
            return True
        return any(marker in normalized for marker in en_markers)

    @classmethod
    def _merge_followup_questions_for_plan(
        cls,
        questions: List[str],
        *,
        language: Optional[str],
        max_items: int,
    ) -> List[str]:
        """Normalize follow-up questions into an internal deferred plan list."""

        seen: set[str] = set()
        merged: List[str] = []
        for raw in questions or []:
            text = " ".join(str(raw or "").strip().split())
            if not text:
                continue
            if cls._looks_like_user_clarification_followup(text):
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
            if len(merged) >= max(1, int(max_items or 1)):
                break
        if merged:
            return merged
        fallback = (
            "Refine evidence coverage for unresolved sub-questions"
            if language != "zh"
            else "补充未覆盖子问题的证据并细化结论"
        )
        return [fallback]

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
        llm_provider_override: Optional[str] = None,
        llm_model_override: Optional[str] = None,
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> str:
        """Refine the report using an LLM.

        Args:
            topic (str): Research topic.
            language (str): Report language code.
            outline (List[str]): Outline lines.
            notes (List[str]): Research notes.
            citation_table (List[str]): Reference table lines.
            context_text (Optional[str]): Optional conversation context.

        Returns:
            str: LLM-generated report.
        """

        report_endpoints = resolve_llm_endpoints(
            provider_override=llm_provider_override,
            model_name_override=llm_model_override,
            allow_request_override=True,
        )
        report_primary = report_endpoints[0]
        client = LLMClient(
            api_key=report_primary.api_key,
            base_url=report_primary.base_url,
            model_name=report_primary.model_name,
            temperature=settings.REPORT_LLM_TEMPERATURE,
            max_tokens=settings.REPORT_LLM_MAX_TOKENS,
            timeout=self._request_timeout,
            usage_callback=usage_callback,
            usage_label="report_refiner",
            endpoint_chain=report_endpoints,
            provider=report_primary.provider,
        )
        context_window = LLMClient.estimate_context_window_tokens(report_primary.model_name)
        input_budget = min(
            int(getattr(settings, "REPORT_PROMPT_MAX_INPUT_TOKENS", 16000) or 16000),
            max(2400, context_window - int(settings.REPORT_LLM_MAX_TOKENS) - 1200),
        )
        refiner = ReportRefiner(client, language=language)
        return await refiner.refine(
            topic=topic,
            outline=outline,
            notes=notes,
            citation_table=citation_table,
            report_style=report_style,
            context_text=context_text,
            input_token_budget=input_budget,
        )

    async def _refine_report_sectional(
        self,
        *,
        store: StateStore,
        reporter: ReporterAgent,
        queue: DynamicTopicQueue,
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
        llm_provider_override: Optional[str] = None,
        llm_model_override: Optional[str] = None,
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> str:
        """Generate a refined report section-by-section.

        This mode emits progress events per section and updates `report.json` so the
        frontend can preview partial report content while the run is still running.

        Args:
            store (StateStore): State persistence helper.
            reporter (ReporterAgent): Report renderer.
            queue (DynamicTopicQueue): Full topic queue with block evidence.
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
            str: LLM-generated report.
        """

        section_endpoints = resolve_llm_endpoints(
            provider_override=llm_provider_override,
            model_name_override=llm_model_override,
            allow_request_override=True,
        )
        section_primary = section_endpoints[0]
        client = LLMClient(
            api_key=section_primary.api_key,
            base_url=section_primary.base_url,
            model_name=section_primary.model_name,
            temperature=settings.REPORT_LLM_TEMPERATURE,
            max_tokens=settings.REPORT_LLM_MAX_TOKENS,
            timeout=self._request_timeout,
            usage_callback=usage_callback,
            usage_label="report_section",
            endpoint_chain=section_endpoints,
            provider=section_primary.provider,
        )

        builder = ReportTemplateBuilder(language=language)
        sections = builder.build_sections()
        total = len(sections)
        if total == 0:
            raise RuntimeError("Report section template is empty.")

        title_prefix = "深度研究报告：" if language == "zh" else "DeepResearch Report:"
        header = f"# {title_prefix} {topic}".strip()
        references_section = reporter.render_references_section(
            max_items=max(
                1,
                int(getattr(settings, "REPORT_REFERENCES_MAX_TOTAL", 80) or 80),
            )
        )
        section_max_tokens = int(getattr(settings, "REPORT_LLM_SECTION_MAX_TOKENS", 1024) or 1024)
        section_context_window = LLMClient.estimate_context_window_tokens(section_primary.model_name)
        section_input_budget = min(
            int(getattr(settings, "REPORT_SECTION_PROMPT_MAX_INPUT_TOKENS", 9000) or 9000),
            max(1800, section_context_window - section_max_tokens - 1200),
        )
        section_max_blocks = int(getattr(settings, "REPORT_SECTION_MAX_BLOCKS", 7) or 7)
        section_max_notes_per_block = int(
            getattr(settings, "REPORT_SECTION_MAX_NOTES_PER_BLOCK", 4) or 4
        )
        section_max_notes_total = int(getattr(settings, "REPORT_SECTION_MAX_NOTES_TOTAL", 28) or 28)
        section_max_citations = int(getattr(settings, "REPORT_SECTION_MAX_CITATIONS", 48) or 48)

        generated: List[str] = []
        self._emit_progress(
            store,
            research_id,
            "reporting",
            "LLM sectional report generation started",
            {
                "sections_total": total,
                "section_max_tokens": section_max_tokens,
                "section_input_budget_tokens": section_input_budget,
                "provider": section_primary.provider,
                "model": section_primary.model_name,
            },
        )

        for idx, section in enumerate(sections, start=1):
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "LLM section started",
                {"section_index": idx, "sections_total": total, "section_title": section.title},
            )
            evidence_pack = reporter.build_section_evidence_pack(
                queue=queue,
                topic=topic,
                section_title=section.title,
                section_guidance=section.guidance,
                max_blocks=section_max_blocks,
                max_notes_per_block=section_max_notes_per_block,
                max_total_notes=section_max_notes_total,
                max_citations=section_max_citations,
            )
            section_outline = evidence_pack.outline or outline
            section_notes = evidence_pack.notes or notes
            section_citation_table = evidence_pack.citation_table or citation_table
            self._emit_progress(
                store,
                research_id,
                "reporting",
                "LLM section evidence prepared",
                {
                    "section_index": idx,
                    "sections_total": total,
                    "section_title": section.title,
                    "blocks": len(evidence_pack.block_ids),
                    "notes": len(section_notes),
                    "citations": len(section_citation_table),
                },
            )
            previous_text = "\n\n".join(generated).strip()

            prompt = builder.build_section_prompt(
                topic=topic,
                section_title=section.title,
                section_guidance=section.guidance,
                outline=section_outline,
                notes=section_notes,
                citation_table=section_citation_table,
                language=language,
                report_style=report_style,
                previous_text=previous_text or None,
                context_text=context_text,
                input_token_budget=section_input_budget,
            )
            raw = await client.generate(prompt, max_tokens=section_max_tokens)
            if not raw:
                raise RuntimeError(
                    f"LLM section generation returned empty output at section '{section.title}'."
                )
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
                        "notes": section_notes,
                        "citation_table": section_citation_table,
                        "draft_markdown": draft_report,
                        "sectional": True,
                        "section_index": idx,
                        "sections_total": total,
                        "current_section": section.title,
                        "section_block_ids": evidence_pack.block_ids,
                        "section_prompt_input_token_budget": section_input_budget,
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
