"""Utilities for analyzing report quality signals.

These checks are intentionally lightweight and deterministic so they can run in
CI and production without extra LLM calls. The goal is to surface "evidence-first"
signals (citation coverage) that are useful for debugging and interview demos.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


_CLICKABLE_CITATION_RE = re.compile(r"\[\[(\d+)\]\]\(#ref-\1\)")
_PLACEHOLDER_CITATION_RE = re.compile(r"\[(?:N|n|\?)\]")


def analyze_report(report_markdown: str) -> Dict[str, Any]:
    """Compute simple quality metrics from a report markdown string.

    Args:
        report_markdown (str): Final report markdown.

    Returns:
        Dict[str, Any]: Quality metrics (citation coverage, counts).
    """

    main_text, _refs = _split_references_section(report_markdown or "")
    sections = _split_level2_sections(main_text)
    paragraphs = _extract_content_paragraphs(main_text)
    citations_by_para = [_CLICKABLE_CITATION_RE.findall(p) for p in paragraphs]

    paragraphs_total = len(paragraphs)
    paragraphs_with_citations = sum(1 for refs in citations_by_para if refs)
    paragraphs_without_citations = paragraphs_total - paragraphs_with_citations
    mention_count = sum(len(refs) for refs in citations_by_para)
    placeholder_markers_count = len(_PLACEHOLDER_CITATION_RE.findall(main_text))

    distinct: List[int] = []
    seen = set()
    for refs in citations_by_para:
        for ref in refs:
            try:
                ref_num = int(ref)
            except ValueError:
                continue
            if ref_num in seen:
                continue
            seen.add(ref_num)
            distinct.append(ref_num)

    coverage = None
    if paragraphs_total:
        coverage = paragraphs_with_citations / paragraphs_total

    uncited_examples: List[str] = []
    for paragraph, refs in zip(paragraphs, citations_by_para):
        if refs:
            continue
        snippet = re.sub(r"\s+", " ", paragraph).strip()
        if len(snippet) > 140:
            snippet = f"{snippet[:140].rstrip()}..."
        if snippet:
            uncited_examples.append(snippet)
        if len(uncited_examples) >= 3:
            break

    section_stats: List[Dict[str, Any]] = []
    for section in sections:
        section_paragraphs = _extract_content_paragraphs(section.get("content") or "")
        section_citations = [_CLICKABLE_CITATION_RE.findall(p) for p in section_paragraphs]
        total = len(section_paragraphs)
        with_cit = sum(1 for refs in section_citations if refs)
        cov = None if total == 0 else with_cit / total
        mentions = sum(len(refs) for refs in section_citations)
        section_stats.append(
            {
                "title": section.get("title") or "",
                "paragraphs_total": total,
                "paragraphs_with_citations": with_cit,
                "citation_paragraph_coverage": cov,
                "citations_mentions": mentions,
            }
        )
    sections_without_citations = [
        sec["title"]
        for sec in section_stats
        if sec.get("paragraphs_total") and (sec.get("paragraphs_with_citations") or 0) == 0
    ]

    return {
        "paragraphs_total": paragraphs_total,
        "paragraphs_with_citations": paragraphs_with_citations,
        "paragraphs_without_citations": paragraphs_without_citations,
        "citation_paragraph_coverage": coverage,
        "citations_mentions": mention_count,
        "placeholder_citation_markers": placeholder_markers_count,
        "citations_distinct_count": len(distinct),
        "citations_distinct": distinct,
        "uncited_examples": uncited_examples,
        "sections": section_stats,
        "sections_without_citations": sections_without_citations,
    }


def _split_references_section(text: str) -> Tuple[str, str]:
    """Split markdown into main body and references section if present."""

    lowered = (text or "").lower()
    markers = ["## references", "## 参考文献", "## 參考文獻"]
    cut = -1
    for marker in markers:
        idx = lowered.find(marker)
        if idx != -1:
            cut = idx
            break
    if cut == -1:
        return text, ""
    return text[:cut].strip(), text[cut:].strip()


def _extract_content_paragraphs(text: str) -> List[str]:
    """Extract non-heading paragraphs for coverage calculation."""

    chunks = [chunk.strip() for chunk in (text or "").split("\n\n") if chunk.strip()]
    paragraphs: List[str] = []
    for chunk in chunks:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        # Skip pure heading blocks like "## Something".
        if len(lines) == 1 and lines[0].startswith("#"):
            continue
        paragraphs.append(chunk)
    return paragraphs


def _split_level2_sections(text: str) -> List[Dict[str, str]]:
    """Split markdown text by level-2 headings (## ...).

    Args:
        text (str): Markdown body without references.

    Returns:
        List[Dict[str, str]]: Sections with `title` and `content`.
    """

    sections: List[Dict[str, str]] = []
    current_title = ""
    buffer: List[str] = []

    for line in (text or "").splitlines():
        if line.startswith("## "):
            if buffer:
                sections.append({"title": current_title, "content": "\n".join(buffer).strip()})
            current_title = line.replace("## ", "", 1).strip()
            buffer = []
            continue
        buffer.append(line)

    if buffer:
        sections.append({"title": current_title, "content": "\n".join(buffer).strip()})

    # Drop the preface section if it has no title and is basically empty.
    return [sec for sec in sections if (sec.get("title") or sec.get("content"))]

