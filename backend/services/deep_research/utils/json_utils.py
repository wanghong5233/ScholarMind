"""JSON parsing utilities for LLM outputs."""

from __future__ import annotations

import json
import re
from typing import Any, Optional


def extract_json_from_text(text: str) -> Any:
    """Extract JSON object or array from a text blob.

    Args:
        text (str): Raw LLM output.

    Returns:
        Any: Parsed JSON payload or None if not found.
    """

    if not text:
        return None
    cleaned = text.strip()
    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if code_block:
        snippet = code_block.group(1).strip()
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start == -1 or end == -1 or end <= start:
            continue
        snippet = cleaned[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            continue
    return None


def ensure_json_dict(value: Any) -> Optional[dict]:
    """Return dict when payload is a dict.

    Args:
        value (Any): Parsed payload.

    Returns:
        Optional[dict]: Dict payload or None.
    """

    if isinstance(value, dict):
        return value
    return None


def ensure_json_list(value: Any) -> Optional[list]:
    """Return list when payload is a list.

    Args:
        value (Any): Parsed payload.

    Returns:
        Optional[list]: List payload or None.
    """

    if isinstance(value, list):
        return value
    return None


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce a value into a boolean.

    Args:
        value (Any): Raw value.
        default (bool): Default fallback.

    Returns:
        bool: Normalized boolean.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "y"}:
            return True
        if lowered in {"false", "no", "0", "n"}:
            return False
    return default


def coerce_str_list(value: Any) -> list[str]:
    """Coerce a value into a list of non-empty strings.

    Args:
        value (Any): Raw value.

    Returns:
        list[str]: Normalized string list.
    """

    if isinstance(value, list):
        items = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text)
        return items
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []
