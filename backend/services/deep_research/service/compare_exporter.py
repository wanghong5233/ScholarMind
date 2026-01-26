"""Utilities to export DeepResearch run comparisons."""

from __future__ import annotations

from typing import Any, Dict

from schemas.common import DeepResearchCompareResponse


def render_compare_markdown(payload: DeepResearchCompareResponse) -> str:
    """Render a comparison payload into markdown."""

    left = payload.left
    right = payload.right
    diff = payload.diff or {}

    lines = [
        "# DeepResearch Comparison",
        "",
        f"- Left: `{left.research_id}` ({left.topic or '-'})",
        f"- Right: `{right.research_id}` ({right.topic or '-'})",
        "",
        "## Summary",
        "",
        "| Metric | Left | Right | Delta |",
        "| --- | --- | --- | --- |",
    ]
    metrics = [
        "duration_seconds",
        "blocks_total",
        "citations_total",
        "tool_traces_total",
        "decisions_total",
        "errors_total",
    ]
    for metric in metrics:
        entry = diff.get(metric, {})
        lines.append(
            f"| {metric} | {format_value(entry.get('left'))} | "
            f"{format_value(entry.get('right'))} | {format_value(entry.get('delta'))} |"
        )

    blocks_by_status = diff.get("blocks_by_status") or {}
    if blocks_by_status:
        lines.extend(["", "## Blocks By Status", "", "| Status | Left | Right | Delta |", "| --- | --- | --- | --- |"])
        for status, entry in sorted(blocks_by_status.items()):
            lines.append(
                f"| {status} | {format_value(entry.get('left'))} | "
                f"{format_value(entry.get('right'))} | {format_value(entry.get('delta'))} |"
            )

    tools_by_type = diff.get("tool_traces_by_type") or {}
    if tools_by_type:
        lines.extend(["", "## Tool Traces By Type", "", "| Tool | Left | Right | Delta |", "| --- | --- | --- | --- |"])
        for tool_name, entry in sorted(tools_by_type.items()):
            lines.append(
                f"| {tool_name} | {format_value(entry.get('left'))} | "
                f"{format_value(entry.get('right'))} | {format_value(entry.get('delta'))} |"
            )

    top_tools = diff.get("top_tools") or {}
    if top_tools:
        lines.extend(["", "## Top Tools", ""])
        for side in ("left", "right"):
            tools = top_tools.get(side) or {}
            if not tools:
                continue
            lines.append(f"- {side}: " + ", ".join([f"{k}({v})" for k, v in tools.items()]))

    top_errors = diff.get("top_errors") or {}
    if top_errors:
        lines.extend(["", "## Top Errors", ""])
        for side in ("left", "right"):
            errors = top_errors.get(side) or []
            if not errors:
                continue
            lines.append(f"- {side}:")
            for err in errors:
                summary = err.get("summary") or err.get("message") or str(err)
                lines.append(f"  - {summary}")

    top_status = diff.get("blocks_by_status_top") or []
    if top_status:
        lines.extend(["", "## Top Status Changes", ""])
        for entry in top_status:
            lines.append(
                f"- {entry.get('status')}: "
                f"{format_value(entry.get('left'))} → {format_value(entry.get('right'))} "
                f"(Δ {format_value(entry.get('delta'))})"
            )

    return "\n".join(lines)


def format_value(value: Any) -> str:
    """Format numbers in a human-friendly way."""

    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (int,)):
        return str(value)
    return str(value)
