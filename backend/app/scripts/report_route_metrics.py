from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class RouteEvent:
    ts: int
    question: str
    route_type: str
    route_reason: str
    route_confidence: float
    retrieval_disabled: bool
    route_applied: bool
    route_skip: bool
    index_mode: str
    hits: int
    stream: bool
    total_ms: float | None
    retrieval_ms: float | None
    generation_ms: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build route metrics report from ask_events logs.")
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=Path("/app/logs"),
        help="Directory containing ask_events.*.jsonl",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="ask_events.*.jsonl",
        help="Glob pattern for log files.",
    )
    parser.add_argument(
        "--start-ts",
        type=int,
        default=0,
        help="Start timestamp in milliseconds (inclusive).",
    )
    parser.add_argument(
        "--end-ts",
        type=int,
        default=0,
        help="End timestamp in milliseconds (inclusive). 0 means no upper bound.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON report.",
    )
    return parser.parse_args()


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * max(0.0, min(1.0, p))
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def parse_event(payload: dict[str, Any]) -> RouteEvent:
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    intent = route.get("intent") if isinstance(route.get("intent"), dict) else {}
    timing = payload.get("timing") if isinstance(payload.get("timing"), dict) else {}

    route_type = str(payload.get("route_type") or route.get("type") or "unknown").strip() or "unknown"
    route_reason = str(payload.get("route_reason") or route.get("reason") or "unknown").strip() or "unknown"
    route_confidence = safe_float(payload.get("route_confidence"))
    if route_confidence is None:
        route_confidence = safe_float(route.get("confidence"))
    route_confidence = route_confidence if route_confidence is not None else 0.0
    retrieval_disabled_raw = payload.get("retrieval_disabled")
    if retrieval_disabled_raw is None:
        retrieval_disabled_raw = route.get("retrieval_disabled")
    retrieval_disabled = bool(retrieval_disabled_raw)
    index_mode = str(payload.get("index_mode") or payload.get("indexMode") or "unknown")
    if route_type == "unknown":
        if retrieval_disabled:
            route_type = "chat_only"
        elif index_mode == "session_only":
            route_type = "rag_session"
        elif index_mode == "global_only":
            route_type = "rag_global"
        elif index_mode == "hybrid":
            route_type = "rag_hybrid"
    if route_reason == "unknown":
        route_reason = "legacy_missing_route"

    return RouteEvent(
        ts=int(payload.get("ts") or 0),
        question=str(payload.get("question") or ""),
        route_type=route_type,
        route_reason=route_reason,
        route_confidence=max(0.0, min(1.0, float(route_confidence))),
        retrieval_disabled=retrieval_disabled,
        route_applied=bool(intent.get("applied", False)),
        route_skip=bool(intent.get("skip", False)),
        index_mode=index_mode,
        hits=int(payload.get("hits") or 0),
        stream=bool(payload.get("stream", False)),
        total_ms=safe_float(timing.get("total_ms")),
        retrieval_ms=safe_float(timing.get("retrieval_ms")),
        generation_ms=safe_float(timing.get("generation_ms")),
    )


def iter_events(
    *,
    logs_dir: Path,
    glob_pattern: str,
    start_ts: int,
    end_ts: int,
) -> Iterable[RouteEvent]:
    for file_path in sorted(logs_dir.glob(glob_pattern)):
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    text = raw.strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    ts = int(payload.get("ts") or 0)
                    if ts <= 0:
                        continue
                    if ts < start_ts:
                        continue
                    if end_ts > 0 and ts > end_ts:
                        continue
                    yield parse_event(payload)
        except OSError:
            continue


def build_report(events: list[RouteEvent]) -> dict[str, Any]:
    route_type_counter = Counter(event.route_type for event in events)
    route_reason_counter = Counter(event.route_reason for event in events)
    index_mode_counter = Counter(event.index_mode for event in events)

    total_ms_values = [event.total_ms for event in events if event.total_ms is not None]
    retrieval_ms_values = [event.retrieval_ms for event in events if event.retrieval_ms is not None]
    generation_ms_values = [event.generation_ms for event in events if event.generation_ms is not None]

    by_route_type_confidence: dict[str, list[float]] = defaultdict(list)
    for event in events:
        by_route_type_confidence[event.route_type].append(event.route_confidence)

    by_route_type_stats = {}
    for route_type, confidences in by_route_type_confidence.items():
        by_route_type_stats[route_type] = {
            "count": len(confidences),
            "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        }

    total = len(events)
    stream_count = sum(1 for event in events if event.stream)
    applied_count = sum(1 for event in events if event.route_applied)
    skip_count = sum(1 for event in events if event.route_skip)
    retrieval_disabled_count = sum(1 for event in events if event.retrieval_disabled)

    report = {
        "total": total,
        "stream_count": stream_count,
        "non_stream_count": total - stream_count,
        "route_type_distribution": dict(route_type_counter),
        "route_reason_distribution": dict(route_reason_counter),
        "index_mode_distribution": dict(index_mode_counter),
        "adaptive_applied_ratio": round(applied_count / total, 4) if total else 0.0,
        "adaptive_skip_ratio": round(skip_count / total, 4) if total else 0.0,
        "retrieval_disabled_ratio": round(retrieval_disabled_count / total, 4) if total else 0.0,
        "timing": {
            "total_ms": {
                "p50": round(percentile(total_ms_values, 0.5), 2),
                "p95": round(percentile(total_ms_values, 0.95), 2),
                "avg": round(sum(total_ms_values) / len(total_ms_values), 2) if total_ms_values else 0.0,
            },
            "retrieval_ms": {
                "p50": round(percentile(retrieval_ms_values, 0.5), 2),
                "p95": round(percentile(retrieval_ms_values, 0.95), 2),
                "avg": round(sum(retrieval_ms_values) / len(retrieval_ms_values), 2)
                if retrieval_ms_values
                else 0.0,
            },
            "generation_ms": {
                "p50": round(percentile(generation_ms_values, 0.5), 2),
                "p95": round(percentile(generation_ms_values, 0.95), 2),
                "avg": round(sum(generation_ms_values) / len(generation_ms_values), 2)
                if generation_ms_values
                else 0.0,
            },
        },
        "route_type_stats": by_route_type_stats,
    }
    return report


def print_human_report(report: dict[str, Any], *, start_ts: int, end_ts: int) -> None:
    def format_ts(ms: int) -> str:
        if ms <= 0:
            return "-"
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

    print(f"Window: {format_ts(start_ts)} ~ {format_ts(end_ts) if end_ts > 0 else '-'}")
    print(f"Total: {report['total']} (stream={report['stream_count']}, non_stream={report['non_stream_count']})")
    print(
        "Adaptive: "
        f"applied_ratio={report['adaptive_applied_ratio']:.4f}, "
        f"skip_ratio={report['adaptive_skip_ratio']:.4f}, "
        f"retrieval_disabled_ratio={report['retrieval_disabled_ratio']:.4f}"
    )
    print("Route types:")
    for key, value in sorted(report["route_type_distribution"].items(), key=lambda item: item[1], reverse=True):
        print(f"- {key}: {value}")
    print("Route reasons:")
    for key, value in sorted(report["route_reason_distribution"].items(), key=lambda item: item[1], reverse=True):
        print(f"- {key}: {value}")
    timing = report["timing"]
    print(
        "Timing(ms): "
        f"total p50/p95={timing['total_ms']['p50']}/{timing['total_ms']['p95']} "
        f"retrieval p50/p95={timing['retrieval_ms']['p50']}/{timing['retrieval_ms']['p95']} "
        f"generation p50/p95={timing['generation_ms']['p50']}/{timing['generation_ms']['p95']}"
    )


def main() -> None:
    args = parse_args()
    logs_dir = args.logs_dir
    if not logs_dir.exists():
        candidate = Path(__file__).resolve().parents[1] / "logs"
        if candidate.exists():
            logs_dir = candidate
    events = list(
        iter_events(
            logs_dir=logs_dir,
            glob_pattern=args.glob,
            start_ts=max(0, int(args.start_ts)),
            end_ts=max(0, int(args.end_ts)),
        )
    )
    report = build_report(events)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print_human_report(report, start_ts=max(0, int(args.start_ts)), end_ts=max(0, int(args.end_ts)))


if __name__ == "__main__":
    main()
