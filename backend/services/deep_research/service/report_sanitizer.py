"""Helpers for sanitizing report citations."""

import re
from typing import Iterable


_CITATION_PATTERN = re.compile(r"\[\[?(\d+)\]?\](?:\(#ref-\1\))?")


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
