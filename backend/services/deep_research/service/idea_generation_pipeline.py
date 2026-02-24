"""Idea generation pipeline built on ScholarMind RAG."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import settings
from schemas.common import CitationOut
from schemas.idea_generation import (
    IdeaCandidate,
    IdeaGenerationItem,
    IdeaGenerationNote,
    IdeaGenerationRequest,
    IdeaGenerationResponse,
    IdeaGenerationStatus,
)
from service.citation_manager import AsyncCitationManagerWrapper, CitationManager
from service.citation_utils import register_rag_citations
from service.rag_client import RAGClient
from service.state_store import StateStore
from utils.json_utils import coerce_str_list, ensure_json_dict, ensure_json_list, extract_json_from_text
from utils.language import guess_language
from utils.prompt_loader import load_prompt_bundle


class IdeaGenerationPipeline:
    """Generate research ideas using a multi-stage workflow."""

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
        resolved_topic = self._resolve_topic(request)
        request = request.model_copy(update={"topic": resolved_topic})
        language = self._resolve_language(request)
        prompts = load_prompt_bundle("ideagen", language)
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
                ideas_markdown="Missing session_id; unable to access conversation context.",
                citations=[],
                ideas=[],
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
            async with RAGClient(self._rag_service_url, timeout=self._request_timeout) as rag_client:
                context_pack = await rag_client.get_context(
                    session_id=request.session_id,
                    user_id=user_id,
                    question=request.topic,
                )
                context_payload = self._build_context_payload(
                    context_pack,
                    language,
                    request.notes,
                )
                store.save_json(
                    "step0_context.json",
                    {
                        "context_meta": context_pack.get("context_meta") or {},
                        "context_text": context_payload["context_text"],
                        "rolling_summary": context_payload["rolling_summary"],
                        "memory_points": context_payload["memory_points"],
                        "notes_text": context_payload.get("notes_text", ""),
                    },
                )

                knowledge_points = await self._extract_knowledge_points(
                    rag_client,
                    citation_manager_async,
                    request,
                    prompts,
                    context_payload,
                    user_id,
                )
                store.save_json(
                    "step1_knowledge_points.json",
                    {"knowledge_points": knowledge_points},
                )

                filtered_points = await self._loose_filter(
                    rag_client,
                    citation_manager_async,
                    request,
                    prompts,
                    knowledge_points,
                    user_id,
                )
                store.save_json(
                    "step2_filtered_points.json",
                    {"filtered_points": filtered_points},
                )

                items: List[IdeaGenerationItem] = []
                statements: List[str] = []
                explored_records: List[Dict[str, Any]] = []
                strict_records: List[Dict[str, Any]] = []
                statement_records: List[Dict[str, Any]] = []
                for point in filtered_points:
                    ideas = await self._explore_ideas(
                        rag_client,
                        citation_manager_async,
                        request,
                        prompts,
                        point,
                        user_id,
                    )
                    explored_records.append(
                        {
                            "knowledge_point": point["knowledge_point"],
                            "description": point.get("description", ""),
                            "ideas": [idea.model_dump(mode="json") for idea in ideas],
                        }
                    )
                    kept, rejected, reasons = await self._strict_filter(
                        rag_client,
                        citation_manager_async,
                        request,
                        prompts,
                        point,
                        ideas,
                        user_id,
                    )
                    strict_records.append(
                        {
                            "knowledge_point": point["knowledge_point"],
                            "kept_ideas": kept,
                            "rejected_ideas": rejected,
                            "reasons": reasons,
                        }
                    )
                    statement = await self._generate_statement(
                        rag_client,
                        citation_manager_async,
                        request,
                        prompts,
                        point,
                        kept,
                        reasons,
                        user_id,
                    )
                    statement_records.append(
                        {
                            "knowledge_point": point["knowledge_point"],
                            "statement_markdown": statement,
                        }
                    )
                    item = IdeaGenerationItem(
                        knowledge_point=point["knowledge_point"],
                        description=point.get("description", ""),
                        research_ideas=ideas,
                        kept_ideas=kept,
                        rejected_ideas=rejected,
                        reasons=reasons,
                        statement_markdown=statement,
                    )
                    items.append(item)
                    if statement:
                        statements.append(statement)

                store.save_json("step3_explored_ideas.json", {"items": explored_records})
                store.save_json("step4_strict_filter.json", {"items": strict_records})
                store.save_json("step5_statements.json", {"items": statement_records})

                citation_manager.build_ref_map()
                ideas_markdown = "\n\n".join(statements) or self._build_fallback_markdown(items)
                response = IdeaGenerationResponse(
                    idea_id=idea_id,
                    ideas_markdown=ideas_markdown,
                    citations=[self._to_citation_out(c) for c in citation_manager.list_citations()],
                    ideas=items,
                    trace={
                        "context_meta": context_pack.get("context_meta") or {},
                        "knowledge_points": len(knowledge_points),
                        "filtered_points": len(filtered_points),
                        "ideas": sum(len(item.research_ideas) for item in items),
                    },
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

    async def _extract_knowledge_points(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        request: IdeaGenerationRequest,
        prompts: Dict[str, Any],
        context_payload: Dict[str, str],
        user_id: int,
    ) -> List[Dict[str, str]]:
        language = self._select_language(request)
        system = prompts.get("extract_knowledge_system", "")
        user_template = prompts.get("extract_knowledge_user_template", "")
        prompt = self._build_prompt(
            system,
            user_template,
            topic=request.topic,
            constraints_text=self._format_constraints(request.constraints, language),
            rolling_summary=context_payload.get("rolling_summary", ""),
            memory_points=context_payload.get("memory_points", ""),
            context_text=context_payload.get("context_text", ""),
        )
        payload = await self._ask_json(
            rag_client,
            citation_manager,
            request,
            user_id,
            prompt,
            stage="extract",
        )
        items = self._normalize_knowledge_points(
            payload, request.topic, language=self._select_language(request)
        )
        return self._truncate_points(items)

    async def _loose_filter(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        request: IdeaGenerationRequest,
        prompts: Dict[str, Any],
        knowledge_points: List[Dict[str, str]],
        user_id: int,
    ) -> List[Dict[str, str]]:
        if not knowledge_points:
            return []
        language = self._select_language(request)
        system = prompts.get("loose_filter_system", "")
        user_template = prompts.get("loose_filter_user_template", "")
        points_text = self._format_points_text(knowledge_points, language)
        prompt = self._build_prompt(system, user_template, points_text=points_text)
        payload = await self._ask_json(
            rag_client,
            citation_manager,
            request,
            user_id,
            prompt,
            stage="loose_filter",
        )
        data = ensure_json_dict(payload) or {}
        filtered = data.get("filtered_points")
        normalized = self._normalize_knowledge_points(
            filtered, request.topic, language=self._select_language(request)
        )
        return normalized or knowledge_points

    async def _explore_ideas(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        request: IdeaGenerationRequest,
        prompts: Dict[str, Any],
        point: Dict[str, str],
        user_id: int,
    ) -> List[IdeaCandidate]:
        language = self._select_language(request)
        system = prompts.get("explore_ideas_system", "")
        user_template = prompts.get("explore_ideas_user_template", "")
        idea_min = max(settings.IDEAGEN_MIN_IDEAS_PER_POINT, request.idea_count)
        idea_max = settings.IDEAGEN_MAX_IDEAS_PER_POINT
        prompt = self._build_prompt(
            system,
            user_template,
            knowledge_point=point["knowledge_point"],
            description=point.get("description", ""),
            topic=request.topic,
            constraints_text=self._format_constraints(request.constraints, language),
            idea_count=idea_min,
            idea_max=idea_max,
        )
        payload = await self._ask_json(
            rag_client,
            citation_manager,
            request,
            user_id,
            prompt,
            stage="explore_ideas",
        )
        ideas = self._normalize_ideas(
            payload, point["knowledge_point"], language=self._select_language(request)
        )
        if len(ideas) < idea_min:
            ideas.extend(
                self._fallback_ideas(
                    point["knowledge_point"],
                    idea_min - len(ideas),
                    language=self._select_language(request),
                )
            )
        return ideas[:idea_max]

    async def _strict_filter(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        request: IdeaGenerationRequest,
        prompts: Dict[str, Any],
        point: Dict[str, str],
        ideas: List[IdeaCandidate],
        user_id: int,
    ) -> tuple[List[str], List[str], Dict[str, str]]:
        titles = [idea.title for idea in ideas]
        if len(titles) <= 1:
            return titles, [], {}
        system = prompts.get("strict_filter_system", "")
        user_template = prompts.get("strict_filter_user_template", "")
        ideas_text = "\n".join([f"- {idea.title}: {idea.description}" for idea in ideas])
        prompt = self._build_prompt(
            system,
            user_template,
            knowledge_point=point["knowledge_point"],
            description=point.get("description", ""),
            ideas_text=ideas_text,
        )
        payload = await self._ask_json(
            rag_client,
            citation_manager,
            request,
            user_id,
            prompt,
            stage="strict_filter",
        )
        data = ensure_json_dict(payload) or {}
        kept = [item for item in coerce_str_list(data.get("kept_ideas")) if item in titles]
        rejected = [item for item in coerce_str_list(data.get("rejected_ideas")) if item in titles]
        reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
        if not kept:
            kept = titles[:1]
        if len(titles) >= 5 and len(rejected) < 2:
            for title in titles:
                if title in kept or title in rejected:
                    continue
                rejected.append(title)
                if len(rejected) >= 2:
                    break
        if not rejected:
            rejected = [title for title in titles if title not in kept]
        return kept, rejected, reasons

    async def _generate_statement(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        request: IdeaGenerationRequest,
        prompts: Dict[str, Any],
        point: Dict[str, str],
        kept: List[str],
        reasons: Dict[str, str],
        user_id: int,
    ) -> str:
        system = prompts.get("generate_statement_system", "")
        user_template = prompts.get("generate_statement_user_template", "")
        kept_text = "\n".join([f"- {title}: {reasons.get(title, '')}" for title in kept])
        prompt = self._build_prompt(
            system,
            user_template,
            knowledge_point=point["knowledge_point"],
            description=point.get("description", ""),
            kept_text=kept_text,
        )
        answer = await self._ask_text(
            rag_client,
            citation_manager,
            request,
            user_id,
            prompt,
            stage="statement",
        )
        return answer or self._fallback_statement(point, kept, reasons)

    async def _ask_json(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        request: IdeaGenerationRequest,
        user_id: int,
        prompt: str,
        stage: str,
    ) -> Any:
        answer = await rag_client.ask(
            session_id=request.session_id,
            question=prompt,
            user_id=user_id,
            top_k=request.top_k,
            index_mode=request.index_mode,
            persist_history=False,
        )
        await register_rag_citations(
            rag_citations=answer.citations,
            citation_manager=citation_manager,
            source_id=f"IDEA-{stage}",
        )
        payload = extract_json_from_text(answer.answer or "")
        if payload is not None:
            return payload
        repair_prompt = self._build_repair_prompt(answer.answer or "")
        repaired = await rag_client.ask(
            session_id=request.session_id,
            question=repair_prompt,
            user_id=user_id,
            top_k=request.top_k,
            index_mode=request.index_mode,
            persist_history=False,
        )
        await register_rag_citations(
            rag_citations=repaired.citations,
            citation_manager=citation_manager,
            source_id=f"IDEA-{stage}-repair",
        )
        return extract_json_from_text(repaired.answer or "")

    async def _ask_text(
        self,
        rag_client: RAGClient,
        citation_manager: AsyncCitationManagerWrapper,
        request: IdeaGenerationRequest,
        user_id: int,
        prompt: str,
        stage: str,
    ) -> str:
        answer = await rag_client.ask(
            session_id=request.session_id,
            question=prompt,
            user_id=user_id,
            top_k=request.top_k,
            index_mode=request.index_mode,
            persist_history=False,
        )
        await register_rag_citations(
            rag_citations=answer.citations,
            citation_manager=citation_manager,
            source_id=f"IDEA-{stage}",
        )
        return answer.answer or ""

    @staticmethod
    def _build_prompt(system: str, user_template: str, **kwargs: Any) -> str:
        user_prompt = user_template.format(**kwargs)
        if system:
            return f"{system}\n\n{user_prompt}".strip()
        return user_prompt.strip()

    @staticmethod
    def _build_repair_prompt(raw_text: str) -> str:
        return (
            "你的输出不是有效 JSON。请将以下内容修复为严格 JSON，仅输出 JSON：\n"
            f"{raw_text}"
        )

    def _build_context_payload(
        self,
        pack: Dict[str, Any],
        language: str,
        notes: Optional[List[IdeaGenerationNote]] = None,
    ) -> Dict[str, str]:
        context_text = (pack.get("context_text") or "").strip()
        rolling_summary = (pack.get("rolling_summary") or "").strip()
        memory_items = (pack.get("memory") or {}).get("items") or []
        memory_points = self._format_memory_points(memory_items, language)
        notes_text = self._format_notes_text(notes or [], language)
        if notes_text:
            if context_text:
                context_text = f"{notes_text}\n\n{context_text}"
            else:
                context_text = notes_text
        if context_text:
            context_text = context_text[: settings.IDEAGEN_CONTEXT_MAX_CHARS]
        if not context_text:
            context_text = rolling_summary
        return {
            "context_text": context_text or "",
            "rolling_summary": rolling_summary or "",
            "memory_points": memory_points or "",
            "notes_text": notes_text or "",
        }

    @staticmethod
    def _format_memory_points(memory_items: List[Dict[str, Any]], language: str) -> str:
        if not memory_items:
            return "无" if language == "zh" else "None"
        lines = []
        for item in memory_items:
            text = (
                str(item.get("content") or item.get("summary") or item.get("text") or "")
                .strip()
            )
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if lines else ("无" if language == "zh" else "None")

    @staticmethod
    def _format_notes_text(notes: List[IdeaGenerationNote], language: str) -> str:
        if not notes:
            return ""
        header = "补充笔记：" if language == "zh" else "Notebook notes:"
        lines: List[str] = [header]
        for idx, note in enumerate(notes, 1):
            title = (note.title or "").strip()
            content = (note.content or "").strip()
            if not title and not content:
                continue
            label = title or (f"笔记 {idx}" if language == "zh" else f"Note {idx}")
            lines.append(f"{idx}. {label}")
            if content:
                excerpt = content[: settings.IDEAGEN_NOTE_MAX_CHARS].strip()
                if len(content) > settings.IDEAGEN_NOTE_MAX_CHARS:
                    excerpt = f"{excerpt}..."
                lines.append(excerpt)
            if note.tags:
                tag_label = "标签" if language == "zh" else "Tags"
                lines.append(f"{tag_label}: {', '.join(note.tags)}")
            if note.source:
                source_label = "来源" if language == "zh" else "Source"
                lines.append(f"{source_label}: {note.source}")
        return "\n".join(lines).strip()

    @staticmethod
    def _format_constraints(constraints: List[str], language: Optional[str]) -> str:
        if not constraints:
            return "无" if language == "zh" else "None"
        if language == "zh":
            return "；".join(constraints)
        return "; ".join(constraints)

    @staticmethod
    def _resolve_topic(request: IdeaGenerationRequest) -> str:
        raw_topic = (request.topic or "").strip()
        if raw_topic:
            return raw_topic
        for note in request.notes:
            title = (note.title or "").strip()
            if title:
                return title
        for note in request.notes:
            content = (note.content or "").strip()
            if content:
                first_line = content.splitlines()[0].strip()
                return first_line[:40] if first_line else content[:40]
        if request.language == "zh":
            return "研究方向提炼"
        return "Research idea exploration"

    @staticmethod
    def _resolve_language(request: IdeaGenerationRequest) -> str:
        if request.language:
            return request.language
        for note in request.notes:
            content = (note.content or "").strip()
            if content:
                return guess_language(content)
        return guess_language(request.topic or "")

    @staticmethod
    def _select_language(request: IdeaGenerationRequest) -> str:
        return request.language or guess_language(request.topic)

    @staticmethod
    def _format_points_text(points: List[Dict[str, str]], language: Optional[str]) -> str:
        lines = []
        for idx, point in enumerate(points, 1):
            label = "知识点" if language == "zh" else "Knowledge point"
            lines.append(f"{idx}. {label}: {point['knowledge_point']}\n   {point.get('description', '')}")
        return "\n".join(lines)

    def _normalize_knowledge_points(
        self, payload: Any, fallback_topic: str, language: Optional[str] = None
    ) -> List[Dict[str, str]]:
        items: List[Any] = []
        data = ensure_json_dict(payload) or {}
        if "knowledge_points" in data and isinstance(data["knowledge_points"], list):
            items = data["knowledge_points"]
        else:
            items = ensure_json_list(payload) or []
        normalized: List[Dict[str, str]] = []
        for item in items:
            if isinstance(item, dict):
                title = str(
                    item.get("knowledge_point")
                    or item.get("title")
                    or item.get("name")
                    or ""
                ).strip()
                desc = str(item.get("description") or item.get("summary") or "").strip()
            else:
                title = str(item).strip()
                desc = ""
            if title:
                normalized.append({"knowledge_point": title, "description": desc})
        if not normalized and fallback_topic:
            resolved_lang = language or guess_language(fallback_topic)
            description = (
                "基于主题生成的研究方向"
                if resolved_lang == "zh"
                else "Research direction derived from the topic."
            )
            normalized.append({"knowledge_point": fallback_topic, "description": description})
        return normalized

    def _truncate_points(self, points: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not points:
            return []
        max_points = settings.IDEAGEN_MAX_KNOWLEDGE_POINTS
        min_points = settings.IDEAGEN_MIN_KNOWLEDGE_POINTS
        trimmed = points[:max_points]
        if len(trimmed) < min_points and points:
            trimmed = points[: max(min_points, len(points))]
        return trimmed

    @staticmethod
    def _normalize_ideas(
        payload: Any, fallback_topic: str, language: Optional[str] = None
    ) -> List[IdeaCandidate]:
        items: List[Any] = []
        data = ensure_json_dict(payload) or {}
        if "research_ideas" in data and isinstance(data["research_ideas"], list):
            items = data["research_ideas"]
        else:
            items = ensure_json_list(payload) or []
        ideas: List[IdeaCandidate] = []
        for item in items:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("idea") or "").strip()
                desc = str(item.get("description") or item.get("summary") or "").strip()
                dimension = item.get("dimension")
                novelty = item.get("novelty")
                feasibility = item.get("feasibility")
            else:
                title = str(item).strip()
                desc = ""
                dimension = None
                novelty = None
                feasibility = None
            if title:
                ideas.append(
                    IdeaCandidate(
                        title=title,
                        description=desc,
                        dimension=str(dimension).strip() if dimension else None,
                        novelty=str(novelty).strip() if novelty else None,
                        feasibility=str(feasibility).strip() if feasibility else None,
                    )
                )
        if not ideas and fallback_topic:
            resolved_lang = language or guess_language(fallback_topic)
            if resolved_lang == "zh":
                ideas.append(
                    IdeaCandidate(
                        title=f"{fallback_topic} 的进一步研究",
                        description="基于知识点延伸的研究方向",
                    )
                )
            else:
                ideas.append(
                    IdeaCandidate(
                        title=f"Further research on {fallback_topic}",
                        description="Research directions extended from the knowledge point.",
                    )
                )
        return ideas

    @staticmethod
    def _fallback_ideas(
        topic: str, count: int, language: Optional[str] = None
    ) -> List[IdeaCandidate]:
        resolved_lang = language or guess_language(topic)
        ideas: List[IdeaCandidate] = []
        for idx in range(count):
            if resolved_lang == "zh":
                ideas.append(
                    IdeaCandidate(
                        title=f"{topic} 的扩展方向 {idx + 1}",
                        description="待进一步细化的研究方向",
                    )
                )
            else:
                ideas.append(
                    IdeaCandidate(
                        title=f"{topic} extension {idx + 1}",
                        description="A research direction that needs further refinement.",
                    )
                )
        return ideas

    @staticmethod
    def _fallback_statement(
        point: Dict[str, str], kept: List[str], reasons: Dict[str, str]
    ) -> str:
        lines = [f"## {point['knowledge_point']}"]
        if point.get("description"):
            lines.append("")
            lines.append(f"**知识点回顾：** {point['description']}")
        lines.append("")
        lines.append("**研究想法：**")
        for idx, title in enumerate(kept, 1):
            lines.append("")
            lines.append(f"### {idx}. {title}")
            reason = reasons.get(title)
            if reason:
                lines.append(f"**保留原因：** {reason}")
        return "\n".join(lines)

    @staticmethod
    def _build_fallback_markdown(items: List[IdeaGenerationItem]) -> str:
        if not items:
            return "暂无可用的研究想法。"
        return "\n\n".join([item.statement_markdown or "" for item in items if item.statement_markdown])

    @staticmethod
    def _to_citation_out(citation: Any) -> CitationOut:
        """Convert internal citation metadata to API output."""

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
        """Create a short idea generation id."""

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
