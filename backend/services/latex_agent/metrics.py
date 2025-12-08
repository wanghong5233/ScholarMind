"""
简单的工具执行指标收集
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Dict, Optional


@dataclass
class ToolMetric:
    success: int = 0
    failure: int = 0
    total_duration: float = 0.0


_tool_metrics: Dict[str, ToolMetric] = defaultdict(ToolMetric)
_intent_metrics: Dict[str, Dict[str, int]] = defaultdict(lambda: {"low": 0, "medium": 0, "high": 0})
_plan_metrics: Dict[str, "PlanMetric"] = defaultdict(lambda: PlanMetric())

_workspace_cache_events: Dict[str, int] = defaultdict(int)
_workspace_scan_metric = {"count": 0, "total": 0.0}
_feedback_metrics: Dict[str, int] = defaultdict(int)
_lock = Lock()


@dataclass
class PlanMetric:
    count: int = 0
    total_tools: int = 0
    total_duration: float = 0.0

    def record(self, tool_count: int, duration: float) -> None:
        self.count += 1
        self.total_tools += tool_count
        self.total_duration += duration


def record_tool_metric(tool_name: str, success: bool, duration: float):
    with _lock:
        metric = _tool_metrics[tool_name]
        if success:
            metric.success += 1
        else:
            metric.failure += 1
        metric.total_duration += duration


def record_intent_metric(intent: str, confidence: float):
    bucket = "low"
    if confidence >= 0.8:
        bucket = "high"
    elif confidence >= 0.5:
        bucket = "medium"

    with _lock:
        _intent_metrics[intent][bucket] += 1


def record_plan_metric(intent: str, tool_count: int, duration: float):
    with _lock:
        _plan_metrics[intent].record(tool_count, duration)


def record_workspace_cache_event(event: str):
    with _lock:
        _workspace_cache_events[event] += 1


def record_workspace_scan(duration: float):
    with _lock:
        _workspace_scan_metric["count"] += 1
        _workspace_scan_metric["total"] += duration


def record_user_feedback(rating: str, trace_id: Optional[str] = None):
    with _lock:
        _feedback_metrics[rating] += 1


def format_prometheus_metrics() -> str:
    lines = [
        "# HELP latex_agent_tool_calls_total Number of tool executions.",
        "# TYPE latex_agent_tool_calls_total counter",
    ]
    with _lock:
        for tool_name, metric in _tool_metrics.items():
            lines.append(
                f'latex_agent_tool_calls_total{{tool="{tool_name}",status="success"}} {metric.success}'
            )
            lines.append(
                f'latex_agent_tool_calls_total{{tool="{tool_name}",status="failure"}} {metric.failure}'
            )
        lines.append("# HELP latex_agent_tool_duration_seconds_total Sum of tool execution duration.")
        lines.append("# TYPE latex_agent_tool_duration_seconds_total gauge")
        for tool_name, metric in _tool_metrics.items():
            lines.append(
                f'latex_agent_tool_duration_seconds_total{{tool="{tool_name}"}} {metric.total_duration:.6f}'
            )
        lines.append("# HELP latex_agent_intent_classifications_total Number of intent classifications by confidence.")
        lines.append("# TYPE latex_agent_intent_classifications_total counter")
        for intent, buckets in _intent_metrics.items():
            for bucket, value in buckets.items():
                lines.append(
                    f'latex_agent_intent_classifications_total{{intent="{intent}",confidence="{bucket}"}} {value}'
                )
        lines.append("# HELP latex_agent_plan_build_seconds_total Total time spent building plans.")
        lines.append("# TYPE latex_agent_plan_build_seconds_total gauge")
        for intent, metric in _plan_metrics.items():
            lines.append(
                f'latex_agent_plan_build_seconds_total{{intent="{intent}"}} {metric.total_duration:.6f}'
            )
        lines.append("# HELP latex_agent_plan_build_count Number of plans built per intent.")
        lines.append("# TYPE latex_agent_plan_build_count counter")
        for intent, metric in _plan_metrics.items():
            lines.append(
                f'latex_agent_plan_build_count{{intent="{intent}"}} {metric.count}'
            )
        lines.append("# HELP latex_agent_plan_average_tools Average number of tools per plan.")
        lines.append("# TYPE latex_agent_plan_average_tools gauge")
        for intent, metric in _plan_metrics.items():
            average = metric.total_tools / metric.count if metric.count else 0.0
            lines.append(
                f'latex_agent_plan_average_tools{{intent="{intent}"}} {average:.6f}'
            )
        lines.append("# HELP latex_agent_workspace_cache_events_total Workspace cache events by type.")
        lines.append("# TYPE latex_agent_workspace_cache_events_total counter")
        for event, value in _workspace_cache_events.items():
            lines.append(
                f'latex_agent_workspace_cache_events_total{{event="{event}"}} {value}'
            )
        lines.append("# HELP latex_agent_workspace_scan_duration_seconds_total Total time spent scanning workspace files.")
        lines.append("# TYPE latex_agent_workspace_scan_duration_seconds_total gauge")
        lines.append(
            f'latex_agent_workspace_scan_duration_seconds_total {_workspace_scan_metric["total"]:.6f}'
        )
        lines.append("# HELP latex_agent_workspace_scan_operations_total Number of workspace scans.")
        lines.append("# TYPE latex_agent_workspace_scan_operations_total counter")
        lines.append(
            f'latex_agent_workspace_scan_operations_total {_workspace_scan_metric["count"]}'
        )
        lines.append("# HELP latex_agent_user_feedback_total User feedback counts by rating.")
        lines.append("# TYPE latex_agent_user_feedback_total counter")
        for rating, value in _feedback_metrics.items():
            lines.append(
                f'latex_agent_user_feedback_total{{rating="{rating}"}} {value}'
            )
    return "\n".join(lines) + "\n"

