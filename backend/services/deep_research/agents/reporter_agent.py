"""Reporter agent for building the final DeepResearch report."""

from __future__ import annotations

from typing import List

from service.citation_manager import CitationManager
from service.data_structures import DynamicTopicQueue, TopicBlock, TopicStatus


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
        completed_blocks = [
            block for block in ordered_blocks if block.depth > 0 and block.status == TopicStatus.COMPLETED
        ]
        unresolved_blocks = [
            block for block in ordered_blocks if block.depth > 0 and block.status != TopicStatus.COMPLETED
        ]
        plan_lines = [self._format_outline_line(block) for block in ordered_blocks if block.depth > 0]
        sections = [
            f"# {self._title_prefix()} {topic}",
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

        notes = block.notes or ["No notes collected for this topic."]
        citation_refs = self._format_citation_refs(block)
        return [
            f"{self._block_heading(block.depth)} {block.title}",
            "",
            *notes,
            citation_refs,
            "",
        ]

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
        refs = [self._citation_manager.get_ref_number(cid) for cid in block.citations]
        refs = sorted({ref for ref in refs if isinstance(ref, int)})
        return "Sources: " + " ".join(f"[[{ref}]](#ref-{ref})" for ref in refs)

    def _render_references(self) -> List[str]:
        """Render the references section.

        Returns:
            List[str]: Markdown lines for references.
        """

        citations = sorted(
            self._citation_manager.list_citations(),
            key=lambda c: c.ref_number or 0,
        )
        if not citations:
            return []
        heading = "## 参考文献" if self._language == "zh" else "## References"
        lines = [heading]
        for citation in citations:
            if citation.ref_number is None:
                continue
            ref = citation.ref_number or 0
            title = citation.title or "Untitled"
            source = citation.url or citation.metadata.get("document_name") or ""
            extra = f" ({source})" if source else ""
            lines.append(f"<a id=\"ref-{ref}\"></a>[{ref}] {title}{extra}")
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

    def render_references_section(self) -> str:
        """Render the references section as markdown text."""

        return "\n".join(self._render_references()).strip("\n")

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

    def build_note_feed(self, queue: DynamicTopicQueue) -> List[str]:
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
            notes.extend(block.notes or ["No notes recorded for this topic."])
        return notes

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

    def build_citation_table(self) -> List[str]:
        """Build a citation reference table for prompts.

        Returns:
            List[str]: Citation table lines with ref numbers.
        """

        citations = sorted(
            [c for c in self._citation_manager.list_citations() if c.ref_number is not None],
            key=lambda c: c.ref_number or 0,
        )
        lines: List[str] = []
        for citation in citations:
            ref = citation.ref_number or 0
            title = (citation.title or "Untitled").strip()
            source = (citation.url or citation.metadata.get("document_name") or "").strip()
            snippet = (citation.snippet or "").strip().replace("\n", " ")
            if len(snippet) > 180:
                snippet = f"{snippet[:180].rstrip()}..."
            source_type = (citation.source_type or "").strip()

            parts: List[str] = [f"Cite as [{ref}]: {title}"]
            if source_type:
                parts.append(f"[{source_type}]")
            if source:
                parts.append(f"({source})")
            if snippet:
                parts.append(f"- {snippet}")
            lines.append(" ".join(parts))
        return lines

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
            if block.notes:
                summary_lines.append(block.notes[0])
            if len(summary_lines) >= 3:
                break
        return summary_lines or ["- Summary pending; add more research notes to enrich this section."]

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
