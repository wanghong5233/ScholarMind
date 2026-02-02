"""Notebook note generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid
from typing import Any, Dict, List

from config import settings
from schemas.notebook import NotebookNoteRequest, NotebookNoteResponse
from schemas.common import CitationOut
from service.rag_client import RAGClient
from utils.json_utils import coerce_str_list, ensure_json_dict, extract_json_from_text
from utils.language import guess_language
from utils.prompt_loader import load_prompt_bundle


@dataclass
class NotebookNotePayload:
    """Normalized notebook note payload."""

    title: str
    summary: str
    key_points: List[str]
    questions: List[str]
    tags: List[str]


class NotebookPipeline:
    """Generate structured notebook notes from chat selections."""

    def __init__(self, rag_service_url: str, request_timeout: int) -> None:
        """Initialize the notebook pipeline.

        Args:
            rag_service_url (str): Core RAG service base URL.
            request_timeout (int): Request timeout in seconds.
        """

        self._rag_service_url = rag_service_url
        self._request_timeout = request_timeout

    async def run(self, request: NotebookNoteRequest, user_id: int) -> NotebookNoteResponse:
        """Generate a notebook note based on a selected excerpt.

        Args:
            request (NotebookNoteRequest): Note generation request.
            user_id (int): Current user id.

        Returns:
            NotebookNoteResponse: Generated markdown note payload.
        """

        selection = (request.selection or "").strip()
        if not selection:
            return NotebookNoteResponse(note_markdown="", citations=[], trace={"error": "empty_selection"})
        if not request.session_id:
            return NotebookNoteResponse(
                note_markdown="",
                citations=[],
                trace={"error": "missing_session_id"},
            )

        language = request.language or guess_language(selection)
        prompt_bundle = load_prompt_bundle("notebook", language).get("note_summary") or {}
        prompt = self._build_prompt(
            prompt_bundle.get("system", ""),
            prompt_bundle.get("user", ""),
            selection=self._truncate_text(selection, settings.NOTEBOOK_MAX_SELECTION_CHARS),
            title=request.title or "",
            tags=", ".join(request.tags or []),
            language=language,
        )

        async with RAGClient(self._rag_service_url, timeout=self._request_timeout) as rag_client:
            payload, citations = await self._ask_json(
                rag_client=rag_client,
                request=request,
                prompt=prompt,
                user_id=user_id,
            )

        payload = self._normalize_payload(
            payload,
            request=request,
            selection=selection,
            language=language,
        )
        note_markdown = self._render_markdown(
            payload=payload,
            request=request,
            selection=selection,
            language=language,
            citations=citations,
        )
        return NotebookNoteResponse(
            note_markdown=note_markdown,
            citations=citations,
            trace={
                "language": language,
                "selection_chars": len(selection),
                "tags": payload.tags,
            },
        )

    async def _ask_json(
        self,
        rag_client: RAGClient,
        request: NotebookNoteRequest,
        prompt: str,
        user_id: int,
    ) -> tuple[Any, List[CitationOut]]:
        """Ask the RAG service for JSON output with repair fallback.

        Args:
            rag_client (RAGClient): RAG client.
            request (NotebookNoteRequest): Request payload.
            prompt (str): Prompt text.
            user_id (int): User id.

        Returns:
            tuple[Any, List[CitationOut]]: Parsed JSON payload and citations list.
        """

        answer = await rag_client.ask(
            session_id=request.session_id or "",
            question=prompt,
            user_id=user_id,
            top_k=request.top_k,
            index_mode=request.index_mode,
        )
        payload = extract_json_from_text(answer.answer or "")
        if payload is not None:
            return payload, self._normalize_citations(answer.citations)
        repair_prompt = self._build_repair_prompt(answer.answer or "")
        repaired = await rag_client.ask(
            session_id=request.session_id or "",
            question=repair_prompt,
            user_id=user_id,
            top_k=request.top_k,
            index_mode=request.index_mode,
        )
        return extract_json_from_text(repaired.answer or ""), self._normalize_citations(repaired.citations)

    @staticmethod
    def _build_prompt(system: str, user_template: str, **kwargs: Any) -> str:
        """Build prompt by concatenating system and user templates.

        Args:
            system (str): System prompt prefix.
            user_template (str): User prompt template.
            **kwargs: Template variables.

        Returns:
            str: Combined prompt text.
        """

        user_prompt = user_template.format(**kwargs)
        if system:
            return f"{system}\n\n{user_prompt}".strip()
        return user_prompt.strip()

    @staticmethod
    def _build_repair_prompt(raw_text: str) -> str:
        """Build repair prompt for invalid JSON.

        Args:
            raw_text (str): Raw model output.

        Returns:
            str: Repair prompt.
        """

        return (
            "你的输出不是有效 JSON。请将以下内容修复为严格 JSON，仅输出 JSON：\n"
            f"{raw_text}"
        )

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        """Truncate long text safely.

        Args:
            text (str): Input text.
            limit (int): Max length.

        Returns:
            str: Truncated text.
        """

        if limit <= 0:
            return text
        return text[:limit]

    @staticmethod
    def _normalize_citations(raw: list[Any]) -> List[CitationOut]:
        """Normalize citation payloads.

        Args:
            raw (list[Any]): Raw citation list from RAG.

        Returns:
            List[CitationOut]: Normalized citations.
        """

        citations: List[CitationOut] = []
        for item in raw or []:
            try:
                citations.append(CitationOut.model_validate(item))
            except Exception:
                continue
        return citations

    def _normalize_payload(
        self,
        raw: Any,
        request: NotebookNoteRequest,
        selection: str,
        language: str,
    ) -> NotebookNotePayload:
        """Normalize LLM JSON output.

        Args:
            raw (Any): Parsed JSON payload from the model.
            request (NotebookNoteRequest): Original request payload.
            selection (str): Selected text.
            language (str): Language code.

        Returns:
            NotebookNotePayload: Normalized payload.
        """

        data = ensure_json_dict(raw) or {}
        title = str(data.get("title") or request.title or "").strip()
        if not title:
            title = self._fallback_title(selection, language)
        summary = str(data.get("summary") or "").strip()
        if not summary:
            summary = selection[: settings.NOTEBOOK_SOURCE_EXCERPT_MAX_CHARS].strip()
        key_points = coerce_str_list(data.get("key_points"))[: settings.NOTEBOOK_MAX_KEY_POINTS]
        if not key_points and summary:
            key_points = [summary]
        questions = coerce_str_list(data.get("questions"))[: settings.NOTEBOOK_MAX_QUESTIONS]
        tags = coerce_str_list(data.get("tags")) or coerce_str_list(request.tags)
        if not tags:
            tags = ["研究", "笔记"] if language == "zh" else ["research", "note"]
        return NotebookNotePayload(
            title=title[: settings.NOTEBOOK_TITLE_MAX_CHARS],
            summary=summary,
            key_points=key_points,
            questions=questions,
            tags=tags,
        )

    @staticmethod
    def _fallback_title(selection: str, language: str) -> str:
        """Build a fallback note title.

        Args:
            selection (str): Selected text.
            language (str): Language code.

        Returns:
            str: Fallback title.
        """

        base = selection.strip().replace("\n", " ")
        base = base[:40].strip()
        if not base:
            return "研究笔记" if language == "zh" else "Research Note"
        return base

    def _render_markdown(
        self,
        payload: NotebookNotePayload,
        request: NotebookNoteRequest,
        selection: str,
        language: str,
        citations: List[CitationOut],
    ) -> str:
        """Render markdown from normalized payload.

        Args:
            payload (NotebookNotePayload): Normalized note data.
            request (NotebookNoteRequest): Original request payload.
            selection (str): Selected text.
            language (str): Language code.
            citations (List[CitationOut]): Citations list.

        Returns:
            str: Rendered markdown note.
        """

        note_id = uuid.uuid4().hex
        created_at = datetime.utcnow().isoformat()
        excerpt = selection.strip().replace("\n", " ")
        excerpt = excerpt[: settings.NOTEBOOK_SOURCE_EXCERPT_MAX_CHARS]
        tags_yaml = ", ".join(self._yaml_quote(tag) for tag in payload.tags)
        front_matter = "\n".join(
            [
                "---",
                f'note_id: "{note_id}"',
                f'title: "{self._yaml_escape(payload.title)}"',
                f'summary: "{self._yaml_escape(payload.summary)}"',
                f"tags: [{tags_yaml}]",
                f'session_id: "{self._yaml_escape(request.session_id or "")}"',
                f'created_at: "{created_at}"',
                f'source_excerpt: "{self._yaml_escape(excerpt)}"',
                "---",
            ]
        )
        if language == "zh":
            sections = self._render_sections_zh(payload, request, excerpt, citations)
        else:
            sections = self._render_sections_en(payload, request, excerpt, citations)
        return f"{front_matter}\n\n{sections}".strip()

    @staticmethod
    def _yaml_escape(text: str) -> str:
        """Escape text for YAML inline strings.

        Args:
            text (str): Raw text.

        Returns:
            str: Escaped text.
        """

        return (text or "").replace("\\", "\\\\").replace('"', '\\"')

    def _yaml_quote(self, text: str) -> str:
        """Wrap a tag with YAML-safe quotes.

        Args:
            text (str): Tag text.

        Returns:
            str: YAML-quoted tag.
        """

        return f'"{self._yaml_escape(text)}"'

    def _render_sections_zh(
        self,
        payload: NotebookNotePayload,
        request: NotebookNoteRequest,
        excerpt: str,
        citations: List[CitationOut],
    ) -> str:
        """Render Chinese markdown sections.

        Args:
            payload (NotebookNotePayload): Note payload.
            request (NotebookNoteRequest): Original request.
            excerpt (str): Selection excerpt.
            citations (List[CitationOut]): Citations list.

        Returns:
            str: Rendered sections.
        """
        lines = [
            f"# {payload.title}",
            "",
            "## 摘要",
            payload.summary or "待补充",
            "",
            "## 关键点",
        ]
        lines.extend([f"- {item}" for item in payload.key_points] or ["- 待补充"])
        lines.append("")
        lines.append("## 问题")
        lines.extend([f"- {item}" for item in payload.questions] or ["- 待补充"])
        lines.append("")
        lines.append("## 标签")
        lines.extend([f"- {item}" for item in payload.tags] or ["- 待补充"])
        lines.append("")
        lines.append("## 来源")
        if request.session_id:
            lines.append(f"- 会话: {request.session_id}")
        if excerpt:
            lines.append(f"- 摘录: {excerpt}")
        lines.extend(self._render_citations_block(citations, language="zh"))
        return "\n".join(lines)

    def _render_sections_en(
        self,
        payload: NotebookNotePayload,
        request: NotebookNoteRequest,
        excerpt: str,
        citations: List[CitationOut],
    ) -> str:
        """Render English markdown sections.

        Args:
            payload (NotebookNotePayload): Note payload.
            request (NotebookNoteRequest): Original request.
            excerpt (str): Selection excerpt.
            citations (List[CitationOut]): Citations list.

        Returns:
            str: Rendered sections.
        """
        lines = [
            f"# {payload.title}",
            "",
            "## Summary",
            payload.summary or "TBD",
            "",
            "## Key Points",
        ]
        lines.extend([f"- {item}" for item in payload.key_points] or ["- TBD"])
        lines.append("")
        lines.append("## Questions")
        lines.extend([f"- {item}" for item in payload.questions] or ["- TBD"])
        lines.append("")
        lines.append("## Tags")
        lines.extend([f"- {item}" for item in payload.tags] or ["- TBD"])
        lines.append("")
        lines.append("## Sources")
        if request.session_id:
            lines.append(f"- Session: {request.session_id}")
        if excerpt:
            lines.append(f"- Excerpt: {excerpt}")
        lines.extend(self._render_citations_block(citations, language="en"))
        return "\n".join(lines)

    @staticmethod
    def _render_citations_block(citations: List[CitationOut], language: str) -> List[str]:
        """Render citations block lines.

        Args:
            citations (List[CitationOut]): Citations list.
            language (str): Language code.

        Returns:
            List[str]: Markdown lines for citations.
        """
        if not citations:
            return []
        header = "- 引用:" if language == "zh" else "- Citations:"
        lines = [header]
        for item in citations:
            title = item.title or item.url or "citation"
            ref = item.ref_number or ""
            ref_text = f"[{ref}]" if ref else "-"
            suffix = f" - {item.url}" if item.url else ""
            lines.append(f"  - {ref_text} {title}{suffix}")
        return lines
