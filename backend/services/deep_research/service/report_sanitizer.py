"""Helpers for sanitizing report citations and references."""

import re
from typing import Iterable, List, Optional


_CITATION_PATTERN = re.compile(r"\[\[?(\d+)\]?\](?:\(#ref-\1\))?")
_REFERENCES_HEADINGS = ("## references", "## 参考文献", "## 參考文獻")
# Matches placeholder citation tags that LLMs sometimes emit instead of real refs.
_PLACEHOLDER_CITATION_RE = re.compile(r"\[(?:N|n|\?)\]")
_TABLE_DIVIDER_LINE_RE = re.compile(r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_DUPLICATED_REF_LINK_RE = re.compile(r"(\[\[(\d+)\]\]\(#ref-\2\))(?:\(#ref-\2\))+")
_DANGLING_REF_FRAGMENT_RE = re.compile(r"\(#ref-\d+$", flags=re.MULTILINE)


def strip_placeholder_citation_markers(report: str) -> str:
    """Remove [N], [n], [?] placeholder citation markers from the report.

    These markers are invalid LLM output and must be stripped before the quality
    gate runs; otherwise a report that has BOTH real clickable citations and some
    stray placeholders would fail the quality gate unnecessarily.

    Args:
        report (str): Report markdown content.

    Returns:
        str: Report with placeholder markers removed.
    """
    return _PLACEHOLDER_CITATION_RE.sub("", report or "")


def sanitize_report_markdown_structure(report: str) -> str:
    """Repair common malformed markdown artifacts produced by LLM output.

    Args:
        report (str): Report markdown content.

    Returns:
        str: Structurally sanitized markdown.
    """
    content = str(report or "")
    content = _DUPLICATED_REF_LINK_RE.sub(r"\1", content)
    content = _DANGLING_REF_FRAGMENT_RE.sub("", content)
    return _drop_truncated_table_rows(content)


def _drop_truncated_table_rows(content: str) -> str:
    """Drop malformed markdown table rows that are likely truncated by token limits."""
    lines = str(content or "").splitlines()
    if not lines:
        return str(content or "")

    normalized: List[str] = []
    index = 0
    while index < len(lines):
        current = lines[index]
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if _looks_like_table_header(current) and _is_table_divider(following):
            expected_pipe_count = current.count("|")
            normalized.append(current.rstrip())
            normalized.append(following.rstrip())
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                row = lines[index]
                if _is_complete_table_row(row, expected_pipe_count):
                    normalized.append(row.rstrip())
                index += 1
            continue
        normalized.append(current.rstrip())
        index += 1
    return "\n".join(normalized)


def _looks_like_table_header(line: str) -> bool:
    """Return True when the line resembles a markdown table header row."""
    stripped = str(line or "").strip()
    return stripped.startswith("|") and stripped.count("|") >= 3


def _is_table_divider(line: str) -> bool:
    """Return True when the line matches markdown table divider syntax."""
    return bool(_TABLE_DIVIDER_LINE_RE.fullmatch(str(line or "").strip()))


def _is_complete_table_row(row: str, expected_pipe_count: int) -> bool:
    """Return True when a markdown table row looks complete enough to keep."""
    stripped = str(row or "").strip()
    if not stripped.startswith("|"):
        return False
    if not stripped.endswith("|"):
        return False
    if stripped.count("|") < max(3, expected_pipe_count):
        return False
    return True


def sanitize_citations(report: str, allowed_refs: Iterable[int]) -> str:
    """Remove citations that are not in the allowed reference set.

    Args:
        report (str): Report markdown content.
        allowed_refs (Iterable[int]): Allowed reference numbers.

    Returns:
        str: Cleaned report content.
    """

    allowed = {int(ref) for ref in allowed_refs}

    def _replace(match: re.Match) -> str:
        number = int(match.group(1))
        if number not in allowed:
            return ""
        # Always normalize citations to clickable markdown links so the final report
        # is evidence-first and consistent, regardless of whether the LLM emits
        # [N] or [[N]](#ref-N).
        return f"[[{number}]](#ref-{number})"

    return _CITATION_PATTERN.sub(_replace, report)


def extract_report_reference_numbers(
    report: str,
    *,
    allowed_refs: Optional[Iterable[int]] = None,
) -> List[int]:
    """Extract unique citation numbers used in report main body."""

    body = strip_references_section(report)
    numbers: List[int] = []
    seen: set[int] = set()
    allowed = {int(item) for item in allowed_refs} if allowed_refs is not None else None
    for token in _CITATION_PATTERN.findall(body or ""):
        number = int(token)
        if allowed is not None and number not in allowed:
            continue
        if number in seen:
            continue
        seen.add(number)
        numbers.append(number)
    return numbers


def strip_references_section(report: str) -> str:
    """Remove markdown references section if present."""

    content = str(report or "")
    lowered = content.lower()
    cut = -1
    for marker in _REFERENCES_HEADINGS:
        idx = lowered.find(marker)
        if idx != -1:
            cut = idx
            break
    if cut == -1:
        return content.strip()
    return content[:cut].rstrip()
