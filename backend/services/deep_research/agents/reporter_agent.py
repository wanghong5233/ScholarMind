"""Reporter agent for building the final DeepResearch report."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List, Optional

from service.citation_manager import CitationManager
from service.data_structures import DynamicTopicQueue, TopicBlock, TopicStatus


@dataclass
class SectionEvidencePack:
    """Section-scoped prompt evidence for report generation."""

    outline: List[str]
    notes: List[str]
    citation_table: List[str]
    block_ids: List[str]


class ReporterAgent:
    """Render a research report from completed blocks."""

    def __init__(self, citation_manager: CitationManager, language: str = "en") -> None:
        """Initialize the reporter with language preference.

        Args:
            citation_manager (CitationManager): Citation registry.
            language (str): Report language code.
        """

        self._citation_manager = citation_manager
        self._language = language

    def build_report(self, topic: str, queue: DynamicTopicQueue) -> str:
        """Render the report markdown for a DeepResearch run.

        Args:
            topic (str): Research topic.
            queue (DynamicTopicQueue): Queue with completed blocks.

        Returns:
            str: Markdown report.
        """

        used_citations = self._collect_used_citations(queue)
        self._citation_manager.build_ref_map_for(used_citations)
        ordered_blocks = self._ordered_blocks(queue)
        report_topic = self.resolve_report_topic(topic, queue)
        completed_blocks = [
            block for block in ordered_blocks if block.depth > 0 and block.status == TopicStatus.COMPLETED
        ]
        unresolved_blocks = [
            block for block in ordered_blocks if block.depth > 0 and block.status != TopicStatus.COMPLETED
        ]
        plan_lines = [self._format_outline_line(block) for block in ordered_blocks if block.depth > 0]
        sections = [
            f"# {self._title_prefix()} {report_topic}",
            "",
            f"## {self._section_title('summary')}",
            *self._build_summary(queue),
            "",
            f"## {self._section_title('plan')}",
            *plan_lines,
            "",
            f"## {self._section_title('findings')}",
        ]
        for block in completed_blocks:
            sections.extend(self._render_block(block))
        if unresolved_blocks:
            sections.extend(self._render_unresolved(unresolved_blocks))
        sections.extend(self._render_limitations(queue))
        sections.extend(self._render_future_work(queue))
        sections.extend(self._render_references())
        return "\n".join(sections).strip()

    def resolve_report_topic(self, topic: str, queue: DynamicTopicQueue) -> str:
        """Resolve a concise, human-readable report topic.

        Keep the original request topic for planning/retrieval, but avoid using a long
        instruction prompt as the visible report title.
        """

        normalized_topic = self._normalize_topic_text(topic)
        fallback_topic = self._select_representative_topic_from_queue(
            queue, exclude=normalized_topic
        )
        if self._looks_like_instruction_topic(normalized_topic) or len(normalized_topic) > 72:
            resolved = fallback_topic or normalized_topic
        else:
            resolved = normalized_topic or fallback_topic
        if not resolved:
            resolved = "深度研究主题" if self._language == "zh" else "Research Topic"

        max_chars = 64 if self._language == "zh" else 96
        if len(resolved) > max_chars:
            resolved = f"{resolved[: max_chars - 1].rstrip()}…"
        return resolved

    def _select_representative_topic_from_queue(
        self, queue: DynamicTopicQueue, *, exclude: str = ""
    ) -> str:
        """Pick the first meaningful non-root block title as display topic."""

        ordered_blocks = self._ordered_blocks(queue)
        candidates = [
            block.title
            for block in ordered_blocks
            if block.depth > 0 and block.status == TopicStatus.COMPLETED
        ]
        if not candidates:
            candidates = [block.title for block in ordered_blocks if block.depth > 0]

        for title in candidates:
            normalized = self._normalize_topic_text(title)
            if not normalized:
                continue
            if exclude and normalized == exclude:
                continue
            return normalized
        return ""

    @staticmethod
    def _normalize_topic_text(topic: str) -> str:
        """Normalize spacing/punctuation for a stable title."""

        normalized = re.sub(r"\s+", " ", str(topic or "")).strip()
        return normalized.strip("：:;；,.，。-—")

    @staticmethod
    def _looks_like_instruction_topic(topic: str) -> bool:
        """Heuristic: detect long instruction prompts masquerading as titles."""

        text = str(topic or "").strip()
        if not text:
            return False
        lowered = text.lower()
        markers = (
            "维度如下",
            "我的约束",
            "输出不是",
            "请检索",
            "最后基于",
            "all key judgments",
            "do not",
            "constraints",
        )
        if any(marker in lowered for marker in markers):
            return True
        if re.search(r"\b[1-9][）\)]", text):
            return True
        if text.count("；") >= 2 or text.count(";") >= 2:
            return True
        return False

    def _ordered_blocks(self, queue: DynamicTopicQueue) -> List[TopicBlock]:
        """Return blocks ordered in a parent → children traversal.

        This preserves the hierarchical structure of the topic queue so the report reads
        like a real outline (instead of a flat chronological list).

        Args:
            queue (DynamicTopicQueue): Topic queue.

        Returns:
            List[TopicBlock]: Blocks ordered in tree order.
        """

        blocks = [block for block in queue.list_blocks() if block.depth > 0]
        by_id = {block.block_id: block for block in blocks}
        seen: set[str] = set()
        ordered: List[TopicBlock] = []

        def visit(block: TopicBlock) -> None:
            if block.block_id in seen:
                return
            seen.add(block.block_id)
            ordered.append(block)
            for child_id in block.child_ids:
                child = by_id.get(child_id)
                if not child:
                    continue
                visit(child)

        # Start from depth=1 blocks (planned roots).
        for block in blocks:
            if block.depth != 1:
                continue
            visit(block)

        # Include any dangling blocks (e.g., missing parent mapping).
        for block in blocks:
            if block.block_id not in seen:
                ordered.append(block)
        return ordered

    def _render_block(self, block: TopicBlock) -> List[str]:
        """Render a single block section.

        Args:
            block (TopicBlock): Topic block to render.

        Returns:
            List[str]: Markdown lines for the block.
        """

        notes = self._normalize_block_notes(block.notes)
        if not notes:
            notes = ["No notes collected for this topic."]
        citation_refs = self._format_citation_refs(block)
        lines: List[str] = [f"{self._block_heading(block.depth)} {block.title}", ""]
        for note in notes:
            lines.append(note)
            lines.append("")
        if citation_refs:
            lines.append(citation_refs)
            lines.append("")
        return lines

    def _render_unresolved(self, blocks: List[TopicBlock]) -> List[str]:
        """Render unfinished topic blocks (failed / skipped / pending) for transparency."""

        title = "未完成话题" if self._language == "zh" else "Unresolved Topics"
        lines: List[str] = ["", f"## {title}"]
        for block in blocks:
            status = block.status.value
            notes = block.notes or ["- (no notes recorded)"]
            lines.extend(
                [
                    f"{self._block_heading(block.depth)} {block.title} ({status})",
                    "",
                    *notes,
                    "",
                ]
            )
        return lines

    def _format_citation_refs(self, block: TopicBlock) -> str:
        """Format citation references for a block.

        Args:
            block (TopicBlock): Topic block containing citations.

        Returns:
            str: Inline citation reference line.
        """

        if not block.citations:
            return ""
        refs: List[int] = []
        for citation_id in block.citations:
            citation = self._citation_manager.get_citation(citation_id)
            ref = citation.ref_number if citation is not None else None
            if isinstance(ref, int):
                refs.append(ref)
        refs = sorted(set(refs))
        return "Sources: " + " ".join(f"[[{ref}]](#ref-{ref})" for ref in refs)

    def _render_references(
        self,
        *,
        only_refs: Optional[Iterable[int]] = None,
        max_items: Optional[int] = None,
    ) -> List[str]:
        """Render the references section.

        Returns:
            List[str]: Markdown lines for references.
        """

        only_ref_set: Optional[set[int]] = None
        if only_refs is not None:
            only_ref_set = {int(ref) for ref in only_refs}
        citations = sorted(
            self._citation_manager.list_citations(),
            key=lambda c: c.ref_number or 0,
        )
        if not citations:
            return []
        heading = "## 参考文献" if self._language == "zh" else "## References"
        lines = [heading]
        rendered = 0
        for citation in citations:
            if citation.ref_number is None:
                continue
            ref = citation.ref_number or 0
            if only_ref_set is not None and ref not in only_ref_set:
                continue
            title = citation.title or "Untitled"
            source = citation.url or citation.metadata.get("document_name") or ""
            extra = f" ({source})" if source else ""
            lines.append(f"<a id=\"ref-{ref}\"></a>[{ref}] {title}{extra}")
            rendered += 1
            if max_items is not None and rendered >= max(1, int(max_items)):
                break
        if rendered == 0:
            return []
        return ["", *lines]

    def append_references_if_missing(self, report_markdown: str) -> str:
        """Ensure the report includes reference anchors for clickable citations.

        When the report is generated by an LLM, it may omit the reference section. We
        append one so that `[[N]](#ref-N)` links always resolve.
        """

        if not report_markdown:
            return report_markdown
        if "<a id=\"ref-" in report_markdown:
            return report_markdown
        references = "\n".join(self._render_references()).strip("\n")
        if not references.strip():
            return report_markdown
        return f"{report_markdown.rstrip()}\n{references}\n"

    def render_references_section(
        self,
        *,
        only_refs: Optional[Iterable[int]] = None,
        max_items: Optional[int] = None,
    ) -> str:
        """Render the references section as markdown text."""

        return "\n".join(
            self._render_references(only_refs=only_refs, max_items=max_items)
        ).strip("\n")

    def build_outline(self, queue: DynamicTopicQueue) -> List[str]:
        """Build outline lines for report refinement.

        Args:
            queue (DynamicTopicQueue): Topic queue.

        Returns:
            List[str]: Outline lines.
        """

        return [self._format_outline_line(block) for block in self._ordered_blocks(queue) if block.depth > 0]

    def build_detailed_outline(
        self,
        queue: DynamicTopicQueue,
        *,
        max_key_points_per_topic: int = 2,
    ) -> List[str]:
        """Build a richer outline (topics + key points).

        This produces a three-level outline that is fully grounded in the queue state:
        - Level 1/2: topic titles (by depth)
        - Level 3: key points extracted from collected notes
        """

        lines: List[str] = []
        for block in self._ordered_blocks(queue):
            if block.depth <= 0:
                continue
            indent = "  " * max(0, (block.depth or 1) - 1)
            status_suffix = ""
            if block.status != TopicStatus.COMPLETED:
                status_suffix = f" ({block.status.value})"
            lines.append(f"{indent}- {block.title}{status_suffix}")

            points = self._extract_key_points(block, limit=max_key_points_per_topic)
            for point in points:
                cleaned = self._strip_bullet_prefix(point)
                if not cleaned:
                    continue
                lines.append(f"{indent}  - {cleaned}")
        return lines

    def build_note_feed(
        self,
        queue: DynamicTopicQueue,
        *,
        max_notes_per_block: int = 4,
        max_note_chars: int = 420,
    ) -> List[str]:
        """Flatten notes from completed blocks for LLM input.

        Args:
            queue (DynamicTopicQueue): Topic queue.

        Returns:
            List[str]: Notes ready for prompt construction.
        """

        notes: List[str] = []
        for block in self._ordered_blocks(queue):
            if block.depth <= 0 or block.status != TopicStatus.COMPLETED:
                continue
            notes.append(f"{self._block_heading(block.depth)} {block.title}")
            normalized = self._normalize_block_notes(block.notes, max_items=max_notes_per_block)
            if not normalized:
                normalized = ["No notes recorded for this topic."]
            for line in normalized:
                text = str(line or "").strip()
                if len(text) > max_note_chars:
                    text = f"{text[: max_note_chars - 3].rstrip()}..."
                notes.append(text)
        return notes

    def build_section_evidence_pack(
        self,
        *,
        queue: DynamicTopicQueue,
        topic: str,
        section_title: str,
        section_guidance: str,
        max_blocks: int = 7,
        max_notes_per_block: int = 4,
        max_total_notes: int = 28,
        max_citations: int = 48,
        max_note_chars: int = 420,
        max_snippet_chars: int = 160,
    ) -> SectionEvidencePack:
        """Build section-scoped evidence instead of passing full-run notes.

        This follows a retrieval-like strategy:
        1) rank completed blocks by section relevance;
        2) keep only top blocks for this section;
        3) materialize section-local outline/notes/citations.
        """

        ranked_blocks = self._rank_blocks_for_section(
            queue=queue,
            topic=topic,
            section_title=section_title,
            section_guidance=section_guidance,
            max_blocks=max_blocks,
        )
        if not ranked_blocks:
            return SectionEvidencePack(outline=[], notes=[], citation_table=[], block_ids=[])

        outline: List[str] = []
        notes: List[str] = []
        citation_ids: List[str] = []
        notes_total = 0

        for block in ranked_blocks:
            outline.append(self._format_outline_line(block))
            normalized = self._normalize_block_notes(block.notes, max_items=max_notes_per_block)
            if normalized:
                notes.append(f"{self._block_heading(block.depth)} {block.title}")
            for line in normalized:
                if notes_total >= max(1, max_total_notes):
                    break
                text = str(line or "").strip()
                if not text:
                    continue
                if len(text) > max_note_chars:
                    text = f"{text[: max_note_chars - 3].rstrip()}..."
                notes.append(text)
                notes_total += 1
            for citation_id in block.citations:
                if citation_id not in citation_ids:
                    citation_ids.append(citation_id)
            if notes_total >= max(1, max_total_notes):
                continue

        if not notes:
            notes = self.build_note_feed(
                queue,
                max_notes_per_block=1,
                max_note_chars=max_note_chars,
            )[: max(1, max_total_notes)]

        citation_table = self._build_citation_table_for_ids(
            citation_ids,
            max_items=max_citations,
            max_snippet_chars=max_snippet_chars,
        )
        return SectionEvidencePack(
            outline=outline,
            notes=notes,
            citation_table=citation_table,
            block_ids=[block.block_id for block in ranked_blocks],
        )

    @staticmethod
    def _block_heading(depth: int) -> str:
        """Map a topic depth to a markdown heading marker.

        We keep the top-level report title as `#` and the section titles as `##`.
        For per-topic sections, we start at `###` for depth=1 and indent deeper topics.
        """

        base_level = 3 + max(0, (depth or 1) - 1)
        level = min(6, max(3, base_level))
        return "#" * level

    @staticmethod
    def _format_outline_line(block: TopicBlock) -> str:
        """Format a markdown outline line preserving hierarchy by depth."""

        depth = block.depth or 1
        indent = "  " * max(0, depth - 1)
        return f"{indent}- {block.title}"

    def build_citation_table(
        self,
        *,
        max_items: int = 120,
        max_snippet_chars: int = 120,
    ) -> List[str]:
        """Build a citation reference table for prompts.

        Returns:
            List[str]: Citation table lines with ref numbers.
        """

        citation_ids: List[str] = []
        citations = sorted(
            [c for c in self._citation_manager.list_citations() if c.ref_number is not None],
            key=lambda c: c.ref_number or 0,
        )
        for citation in citations:
            citation_ids.append(citation.citation_id)
        return self._build_citation_table_for_ids(
            citation_ids,
            max_items=max_items,
            max_snippet_chars=max_snippet_chars,
        )

    def _build_citation_table_for_ids(
        self,
        citation_ids: List[str],
        *,
        max_items: int,
        max_snippet_chars: int,
    ) -> List[str]:
        """Build citation table lines for selected citation ids."""

        lines: List[str] = []
        for citation_id in citation_ids:
            if len(lines) >= max(1, max_items):
                break
            citation = self._citation_manager.get_citation(citation_id)
            if citation is None or citation.ref_number is None:
                continue
            ref = citation.ref_number or 0
            title = (citation.title or "Untitled").strip()
            source = (citation.url or citation.metadata.get("document_name") or "").strip()
            snippet = (citation.snippet or "").strip().replace("\n", " ")
            if len(snippet) > max_snippet_chars:
                snippet = f"{snippet[: max_snippet_chars - 3].rstrip()}..."
            source_type = (citation.source_type or "").strip()

            parts: List[str] = [f"Cite as [[{ref}]](#ref-{ref}): {title}"]
            if source_type:
                parts.append(f"[{source_type}]")
            if source:
                parts.append(f"({source})")
            if snippet:
                parts.append(f"- {snippet}")
            lines.append(" ".join(parts))
        return lines

    def _rank_blocks_for_section(
        self,
        *,
        queue: DynamicTopicQueue,
        topic: str,
        section_title: str,
        section_guidance: str,
        max_blocks: int,
    ) -> List[TopicBlock]:
        """Rank completed blocks by section relevance."""

        candidates = [
            block
            for block in self._ordered_blocks(queue)
            if block.depth > 0 and block.status == TopicStatus.COMPLETED
        ]
        if not candidates:
            return []

        query_terms = self._extract_query_terms(f"{topic} {section_title} {section_guidance}")
        ranked: List[tuple[float, TopicBlock]] = []
        for order, block in enumerate(candidates):
            score = self._score_block_for_section(
                block=block,
                query_terms=query_terms,
                section_title=section_title,
                section_guidance=section_guidance,
                order_index=order,
            )
            ranked.append((score, block))

        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = [block for _, block in ranked[: max(1, max_blocks)]]
        if not selected:
            return candidates[: max(1, max_blocks)]
        return selected

    def _score_block_for_section(
        self,
        *,
        block: TopicBlock,
        query_terms: List[str],
        section_title: str,
        section_guidance: str,
        order_index: int,
    ) -> float:
        """Compute lexical relevance score between section intent and block evidence."""

        normalized_notes = self._normalize_block_notes(block.notes, max_items=6)
        haystack = " ".join([block.title, block.question, *normalized_notes]).lower()
        title_text = str(block.title or "").lower()
        section_signature = f"{section_title} {section_guidance}".lower()

        term_hits = sum(1 for term in query_terms if term in haystack)
        title_hits = sum(1 for term in query_terms if term in title_text)
        citation_boost = min(4, len(block.citations)) * 0.35
        note_boost = min(4, len(normalized_notes)) * 0.25
        depth_boost = 0.35 if block.depth == 1 else 0.0
        recency_decay = max(0.0, 0.25 - order_index * 0.01)

        score = term_hits * 1.4 + title_hits * 1.1 + citation_boost + note_boost + depth_boost + recency_decay

        if any(key in section_signature for key in {"方法", "证据", "method", "evidence"}):
            if any(
                key in haystack
                for key in {"方法", "检索", "实验", "数据集", "benchmark", "dataset", "method", "evidence"}
            ):
                score += 1.4
        if any(key in section_signature for key in {"局限", "限制", "limitations", "uncertainty"}):
            if any(
                key in haystack
                for key in {"局限", "限制", "挑战", "不确定", "limitation", "challenge", "uncertainty"}
            ):
                score += 1.2
        if any(key in section_signature for key in {"未来", "后续", "future", "next"}):
            if any(
                key in haystack
                for key in {"未来", "后续", "开放问题", "future", "next step", "open problem"}
            ):
                score += 1.2
        if any(
            key in section_signature
            for key in {
                "可投稿",
                "实验设计",
                "problem definition",
                "experiment design",
                "publishable",
            }
        ):
            if any(
                key in haystack
                for key in {
                    "假设",
                    "创新",
                    "benchmark",
                    "dataset",
                    "baseline",
                    "metric",
                    "ablation",
                    "hypothesis",
                    "novelty",
                    "experiment",
                    "evaluation",
                    "数据集",
                    "基线",
                    "指标",
                    "消融",
                }
            ):
                score += 1.5
        if any(key in section_signature for key in {"背景", "定义", "background", "definition"}):
            if any(key in haystack for key in {"背景", "定义", "机制", "background", "definition", "mechanism"}):
                score += 0.9

        return score

    @staticmethod
    def _extract_query_terms(text: str) -> List[str]:
        """Extract a compact lexical term set for lightweight section retrieval."""

        normalized = str(text or "").lower()
        raw_terms = re.findall(r"[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", normalized)
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "this",
            "that",
            "from",
            "into",
            "about",
            "what",
            "which",
            "where",
            "please",
            "section",
            "report",
            "研究",
            "报告",
            "章节",
            "内容",
            "问题",
        }
        terms: List[str] = []
        for term in raw_terms:
            cleaned = term.strip()
            if not cleaned or cleaned in stop_words:
                continue
            if cleaned not in terms:
                terms.append(cleaned)
            if len(terms) >= 36:
                break
        return terms

    def allowed_reference_numbers(self) -> List[int]:
        """Return allowed reference numbers for report sanitation.

        Returns:
            List[int]: Reference numbers in the citation manager.
        """

        return [
            citation.ref_number
            for citation in self._citation_manager.list_citations()
            if citation.ref_number is not None
        ]

    def _build_summary(self, queue: DynamicTopicQueue) -> List[str]:
        """Construct a short executive summary.

        Args:
            queue (DynamicTopicQueue): Topic queue.

        Returns:
            List[str]: Summary bullet lines.
        """

        summary_lines: List[str] = []
        for block in self._ordered_blocks(queue):
            if block.depth <= 0 or block.status != TopicStatus.COMPLETED:
                continue
            normalized_notes = self._normalize_block_notes(block.notes, max_items=1)
            if normalized_notes:
                summary_lines.append(normalized_notes[0])
            if len(summary_lines) >= 3:
                break
        return summary_lines or ["- Summary pending; add more research notes to enrich this section."]

    @staticmethod
    def _normalize_block_notes(notes: List[str], max_items: int = 6) -> List[str]:
        """Normalize block notes for readable report paragraphs.

        The runtime notes can include raw source dumps (urls/query/debug labels). We keep
        high-signal analytical statements and drop low-signal listing noise.
        """

        normalized: List[str] = []
        for raw in notes or []:
            text = str(raw or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in {"web search highlights:", "paper search highlights:"}:
                continue
            if lowered.startswith(("query:", "sources:", "cite as [")):
                continue
            text = ReporterAgent._strip_bullet_prefix(text)
            if ReporterAgent._looks_like_source_dump(text):
                continue
            normalized.append(text)
            if len(normalized) >= max(1, int(max_items)):
                break
        return normalized

    @staticmethod
    def _looks_like_source_dump(text: str) -> bool:
        """Detect low-signal source listing lines."""

        lowered = str(text or "").lower()
        if "http://" in lowered or "https://" in lowered:
            return True
        if lowered.count(" | ") >= 2:
            return True
        return False

    def _render_limitations(self, queue: DynamicTopicQueue) -> List[str]:
        """Render the limitations section.

        Args:
            queue (DynamicTopicQueue): Topic queue.

        Returns:
            List[str]: Limitation section lines.
        """

        if self._language == "zh":
            return [
                "",
                "## 局限性",
                "- 证据覆盖范围依赖当前知识库与检索配置。",
                "- 部分话题仍需要更深入的迭代研究与验证。",
            ]
        return [
            "",
            "## Limitations",
            "- Evidence coverage depends on the current knowledge base and retrieval settings.",
            "- Some topics may require deeper iterative research for stronger validation.",
        ]

    def _render_future_work(self, queue: DynamicTopicQueue) -> List[str]:
        """Render the future work section.

        Args:
            queue (DynamicTopicQueue): Topic queue.

        Returns:
            List[str]: Future work section lines.
        """

        if self._language == "zh":
            return [
                "",
                "## 未来方向",
                "- 增加后续问题与跨论文对比，扩展话题队列。",
                "- 用更多数据集或实验验证关键结论。",
            ]
        return [
            "",
            "## Future Work",
            "- Expand the topic queue with follow-up questions and cross-paper comparisons.",
            "- Validate key findings with additional datasets or experiments.",
        ]

    def _section_title(self, key: str) -> str:
        """Return localized section titles."""

        if self._language == "zh":
            return {
                "summary": "摘要",
                "plan": "研究计划",
                "findings": "分主题发现",
                "unresolved": "未完成话题",
            }.get(key, key)
        return {
            "summary": "Executive Summary",
            "plan": "Research Plan",
            "findings": "Findings by Topic",
            "unresolved": "Unresolved Topics",
        }.get(key, key)

    def _title_prefix(self) -> str:
        """Return the report title prefix."""

        return "深度研究报告：" if self._language == "zh" else "DeepResearch Report:"

    def _collect_used_citations(self, queue: DynamicTopicQueue) -> List[str]:
        """Collect citation ids in the order they appear in the queue.

        Args:
            queue (DynamicTopicQueue): Topic queue.

        Returns:
            List[str]: Citation ids in appearance order.
        """

        ordered: List[str] = []
        seen = set()
        for block in self._ordered_blocks(queue):
            if block.depth <= 0 or block.status != TopicStatus.COMPLETED:
                continue
            for citation_id in block.citations:
                if citation_id in seen:
                    continue
                seen.add(citation_id)
                ordered.append(citation_id)
        return ordered

    @staticmethod
    def _strip_bullet_prefix(text: str) -> str:
        """Strip a leading bullet marker like '- ' or '* ' for embedding inside outlines."""

        cleaned = (text or "").strip()
        for prefix in ("- ", "* ", "• "):
            if cleaned.startswith(prefix):
                return cleaned[len(prefix) :].strip()
        return cleaned

    def _extract_key_points(self, block: TopicBlock, *, limit: int) -> List[str]:
        """Extract a few high-signal key points from a block's notes."""

        if limit <= 0:
            return []
        points: List[str] = []
        for note in block.notes or []:
            cleaned = (note or "").strip()
            if not cleaned:
                continue
            # Skip internal labels that are helpful in the full note feed but noisy in outlines.
            lowered = cleaned.lower()
            if lowered.startswith(("follow-up", "web search highlights", "code execution")):
                continue
            points.append(cleaned)
            if len(points) >= limit:
                break
        return points
