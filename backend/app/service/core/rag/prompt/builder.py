from __future__ import annotations
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class PromptSection:
    role: str
    content: str


class PromptBuilder:
    """
    Modular prompt builder for RAG:
    - system: global instructions (language, structure, safety)
    - context: retrieved chunks with lightweight source marks
    - instruction: user question
    - style: optional tone/length hints
    """

    def __init__(self, *, language: str = "zh", enable_citations: bool = True, max_context_chars: int = 6000) -> None:
        self.language = language
        self.enable_citations = enable_citations
        self.max_context_chars = max_context_chars

    def build(
        self,
        *,
        question: str,
        chunks: List[Dict[str, Any]],
        style: Optional[str] = None,
        extra_system: Optional[str] = None,
    ) -> List[PromptSection]:
        system = self._build_system(extra_system)
        context = self._build_context(chunks)
        instr = self._build_instruction(question, style)
        sections = [
            PromptSection(role="system", content=system),
            PromptSection(role="system", content=context),
            PromptSection(role="user", content=instr),
        ]
        return sections

    # --- internals ---
    def _build_system(self, extra: Optional[str]) -> str:
        base_zh = (
            "你是严谨的学术助手。请基于提供的上下文回答，不要编造。"
            "若信息不足，请明确说明‘无法确定’。优先给出清晰结构和关键点。"
        )
        base_en = (
            "You are a rigorous academic assistant. Answer strictly based on the provided context. "
            "If insufficient, say 'cannot determine'. Prefer clear structure and key points."
        )
        base = base_zh if self.language == "zh" else base_en
        if self.enable_citations:
            # 统一引用格式：[documentId:page]，例如 [82:1]；多个来源用空格分隔
            base += (
                " 引用格式请统一为 [文档ID:页码]，例如 [82:1]；多个来源用空格分隔，如 [82:1] [81:3]。不要伪造引用。"
                if self.language == "zh"
                else " Use citation format [documentId:page], e.g., [82:1]; separate multiple sources by spaces, e.g., [82:1] [81:3]. Never fabricate citations."
            )
        if extra:
            base += "\n" + extra
        return base

    def _build_context(self, chunks: List[Dict[str, Any]]) -> str:
        # Format: [doc_id:page] content
        buf: List[str] = ["[Context]"]
        total = 0
        for c in chunks:
            md = c.get("metadata", {})
            doc_id = md.get("document_id", "?")
            page = md.get("page", "?")
            text = (c.get("text") or c.get("content") or "").strip()
            line = f"[{doc_id}:{page}] {text}"
            if total + len(line) > self.max_context_chars:
                break
            buf.append(line)
            total += len(line)
        return "\n".join(buf)

    def _build_instruction(self, question: str, style: Optional[str]) -> str:
        if self.language == "zh":
            base = "问题：" + question.strip()
            tail = "\n请基于上文给出要点、结论与必要引用。"
        else:
            base = "Question: " + question.strip()
            tail = "\nPlease answer with key points, conclusion, and necessary citations."
        if style:
            tail += (" 风格：" + style) if self.language == "zh" else (" Style: " + style)
        return base + tail
