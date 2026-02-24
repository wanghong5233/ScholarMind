"""Utilities for handling user-edited plan overrides."""

from __future__ import annotations

from typing import Any, Dict, List

from agents.planner_agent import PlanItem


def extract_plan_override_items(
    metadata: Any,
    *,
    max_depth: int,
    max_breadth: int,
) -> List[PlanItem]:
    """Extract and sanitize editable plan items from request metadata.

    Args:
        metadata (Any): Request metadata payload.
        max_depth (int): Maximum depth allowed by request.
        max_breadth (int): Maximum number of level-one topics.

    Returns:
        List[PlanItem]: Sanitized plan items, or empty list if unavailable/invalid.
    """

    if not isinstance(metadata, dict):
        return []
    raw_items = metadata.get("plan_override_items")
    if not isinstance(raw_items, list):
        return []

    depth_limit = max(1, int(max_depth or 1))
    breadth_limit = max(1, int(max_breadth or 1))

    level_one: List[PlanItem] = []
    nested: List[PlanItem] = []
    last_level_one_title = ""

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        question = str(raw.get("question") or title).strip() or title

        raw_depth = raw.get("depth", 1)
        try:
            depth = int(raw_depth)
        except (TypeError, ValueError):
            depth = 1
        depth = max(1, min(depth_limit, depth))

        parent_title = str(raw.get("parent_title") or "").strip()

        if depth == 1:
            if len(level_one) >= breadth_limit:
                continue
            item = PlanItem(title=title, question=question, depth=1, parent_title=None)
            level_one.append(item)
            last_level_one_title = item.title
            continue

        if not parent_title and last_level_one_title:
            parent_title = last_level_one_title
        if not parent_title:
            continue

        nested.append(
            PlanItem(
                title=title,
                question=question,
                depth=depth,
                parent_title=parent_title,
            )
        )

    if not level_one:
        return []

    # Preserve input order and ensure parent chain exists.
    allowed_titles = {item.title for item in level_one}
    filtered_nested: List[PlanItem] = []
    for item in nested:
        if item.parent_title not in allowed_titles:
            continue
        filtered_nested.append(item)
        allowed_titles.add(item.title)

    return level_one + filtered_nested


def to_plan_item_payload(items: List[PlanItem]) -> List[Dict[str, Any]]:
    """Convert plan items to API payload-friendly dicts."""

    return [
        {
            "title": item.title,
            "question": item.question,
            "depth": item.depth,
            "parent_title": item.parent_title,
        }
        for item in items
    ]
