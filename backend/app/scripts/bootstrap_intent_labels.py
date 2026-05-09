from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap intent routing label set from ask_events logs."
    )
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
        "--output",
        type=Path,
        default=Path(__file__).with_name("intent_routing_eval_real.jsonl"),
        help="Output JSONL for manual verification.",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=300,
        help="Maximum number of records to export.",
    )
    parser.add_argument(
        "--min-question-length",
        type=int,
        default=4,
        help="Minimum question length.",
    )
    return parser.parse_args()


def stable_sort_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized_question(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def load_candidates(
    *,
    logs_dir: Path,
    glob_pattern: str,
    min_question_length: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
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
                    question = str(payload.get("question") or "").strip()
                    if len(question) < max(1, min_question_length):
                        continue
                    normalized = normalized_question(question)
                    if not normalized or normalized in seen_questions:
                        continue

                    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
                    intent = route.get("intent") if isinstance(route.get("intent"), dict) else {}
                    route_type = str(payload.get("route_type") or route.get("type") or "unknown").strip() or "unknown"
                    route_reason = str(payload.get("route_reason") or route.get("reason") or "unknown").strip() or "unknown"
                    route_confidence = route.get("confidence", payload.get("route_confidence", 0.0))
                    retrieval_disabled_raw = payload.get("retrieval_disabled")
                    if retrieval_disabled_raw is None:
                        retrieval_disabled_raw = route.get("retrieval_disabled")
                    final_need_retrieval = not bool(retrieval_disabled_raw)
                    query_type = str(intent.get("query_type") or "unknown").strip().lower()

                    record = {
                        "question": question,
                        "expected_need_retrieval": final_need_retrieval,
                        "expected_query_type": query_type if query_type else "unknown",
                        "human_verified": False,
                        "source": {
                            "ts": payload.get("ts"),
                            "session_id": payload.get("session_id"),
                            "route_type": route_type,
                            "route_reason": route_reason,
                            "route_confidence": route_confidence,
                            "stream": bool(payload.get("stream", False)),
                        },
                        "predicted": {
                            "raw_need_retrieval": bool(intent.get("need_retrieval", True)),
                            "final_need_retrieval": final_need_retrieval,
                            "query_type": query_type,
                            "confidence": intent.get("confidence", 0.0),
                            "reason": intent.get("reason", "unknown"),
                            "applied": bool(intent.get("applied", False)),
                            "skip": bool(intent.get("skip", False)),
                        },
                        "notes": "",
                    }
                    candidates.append(record)
                    seen_questions.add(normalized)
        except OSError:
            continue
    return candidates


def stratified_pick(
    candidates: list[dict[str, Any]],
    *,
    max_samples: int,
) -> list[dict[str, Any]]:
    if max_samples <= 0 or not candidates:
        return []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        route_type = str((item.get("source") or {}).get("route_type") or "unknown")
        groups[route_type].append(item)

    for route_type in groups:
        groups[route_type].sort(key=lambda row: stable_sort_key(str(row.get("question") or "")))

    keys = sorted(groups.keys())
    if not keys:
        return []

    per_group = max(1, max_samples // len(keys))
    selected: list[dict[str, Any]] = []
    for key in keys:
        selected.extend(groups[key][:per_group])

    if len(selected) < max_samples:
        leftovers: list[dict[str, Any]] = []
        for key in keys:
            leftovers.extend(groups[key][per_group:])
        leftovers.sort(key=lambda row: stable_sort_key(str(row.get("question") or "")))
        selected.extend(leftovers[: max_samples - len(selected)])

    return selected[:max_samples]


def main() -> None:
    args = parse_args()
    logs_dir = args.logs_dir
    if not logs_dir.exists():
        candidate = Path(__file__).resolve().parents[1] / "logs"
        if candidate.exists():
            logs_dir = candidate
    candidates = load_candidates(
        logs_dir=logs_dir,
        glob_pattern=args.glob,
        min_question_length=max(1, int(args.min_question_length)),
    )
    selected = stratified_pick(
        candidates,
        max_samples=max(1, int(args.max_samples)),
    )
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"Exported {len(selected)} samples to {output_path}. "
        "Please manually review and flip `human_verified=true` before threshold tuning."
    )


if __name__ == "__main__":
    main()
