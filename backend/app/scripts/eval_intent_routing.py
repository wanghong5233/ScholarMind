from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import sys
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///./routing_eval.db")

from service.core.conversation.routing_decision import (
    classify_query_intent,
    coerce_confidence,
)


@dataclass(frozen=True)
class RoutingSample:
    question: str
    expected_need_retrieval: bool
    expected_query_type: str | None = None


def load_samples(
    path: Path,
    *,
    require_human_verified: bool = False,
) -> tuple[list[RoutingSample], int]:
    samples: list[RoutingSample] = []
    skipped_unverified = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
            if require_human_verified and payload.get("human_verified") is not True:
                skipped_unverified += 1
                continue
            question = str(payload.get("question", "")).strip()
            if not question:
                raise ValueError(f"Line {line_no} missing non-empty question")
            expected_need_retrieval = payload.get("expected_need_retrieval")
            if not isinstance(expected_need_retrieval, bool):
                raise ValueError(f"Line {line_no} expected_need_retrieval must be boolean")
            expected_query_type_raw = payload.get("expected_query_type")
            expected_query_type = (
                str(expected_query_type_raw).strip().lower()
                if isinstance(expected_query_type_raw, str) and expected_query_type_raw.strip()
                else None
            )
            if expected_query_type == "unknown":
                expected_query_type = None
            samples.append(
                RoutingSample(
                    question=question,
                    expected_need_retrieval=expected_need_retrieval,
                    expected_query_type=expected_query_type,
                )
            )
    if not samples:
        raise ValueError(f"No valid samples loaded from {path}")
    return samples, skipped_unverified


def parse_thresholds(raw: str, fallback: float) -> list[float]:
    values: list[float] = []
    for token in (raw or "").split(","):
        item = token.strip()
        if not item:
            continue
        value = coerce_confidence(item, default=fallback)
        values.append(value)
    if fallback not in values:
        values.append(coerce_confidence(fallback, default=0.75))
    return sorted(set(values))


def evaluate_samples(
    *,
    samples: list[RoutingSample],
    retrieval_plan: list[tuple[str, int]],
    threshold: float,
) -> dict[str, Any]:
    tp = 0
    tn = 0
    fp = 0
    fn = 0
    query_type_match = 0
    query_type_total = 0
    confidence_values: list[float] = []
    failures: list[dict[str, Any]] = []

    for sample in samples:
        predicted = classify_query_intent(
            question=sample.question,
            retrieval_plan=retrieval_plan,
        )
        need_retrieval_raw = bool(predicted.get("need_retrieval", True))
        confidence = coerce_confidence(predicted.get("confidence", 0.0), default=0.0)
        route_skip = (not need_retrieval_raw) and confidence >= threshold
        final_need_retrieval = not route_skip
        query_type = str(predicted.get("query_type", "unknown")).strip().lower()

        confidence_values.append(confidence)

        if sample.expected_query_type:
            query_type_total += 1
            if query_type == sample.expected_query_type:
                query_type_match += 1

        expected = sample.expected_need_retrieval
        if expected and final_need_retrieval:
            tp += 1
        elif (not expected) and (not final_need_retrieval):
            tn += 1
        elif expected and (not final_need_retrieval):
            fn += 1
        else:
            fp += 1

        if expected != final_need_retrieval:
            failures.append(
                {
                    "question": sample.question,
                    "expected_need_retrieval": expected,
                    "predicted_need_retrieval": final_need_retrieval,
                    "raw_need_retrieval": need_retrieval_raw,
                    "confidence": confidence,
                    "query_type": query_type,
                    "reason": predicted.get("reason", "unknown"),
                }
            )

    positive_total = tp + fn
    negative_total = tn + fp
    total = len(samples)
    accuracy = (tp + tn) / total if total else 0.0
    false_retrieval_rate = fp / negative_total if negative_total else 0.0
    missed_retrieval_rate = fn / positive_total if positive_total else 0.0
    query_type_accuracy = query_type_match / query_type_total if query_type_total else 0.0
    avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0

    return {
        "threshold": threshold,
        "total": total,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": round(accuracy, 4),
        "false_retrieval_rate": round(false_retrieval_rate, 4),
        "missed_retrieval_rate": round(missed_retrieval_rate, 4),
        "query_type_accuracy": round(query_type_accuracy, 4),
        "avg_confidence": round(avg_confidence, 4),
        "failures": failures,
    }


def print_table(reports: list[dict[str, Any]]) -> None:
    header = (
        "threshold | acc    | FRR(fp/neg) | FNR(fn/pos) | qtype_acc | avg_conf | tp tn fp fn"
    )
    print(header)
    print("-" * len(header))
    for item in reports:
        print(
            f"{item['threshold']:.2f}     | {item['accuracy']:.4f} | "
            f"{item['false_retrieval_rate']:.4f}      | {item['missed_retrieval_rate']:.4f}      | "
            f"{item['query_type_accuracy']:.4f}    | {item['avg_confidence']:.4f}  | "
            f"{item['tp']:>2} {item['tn']:>2} {item['fp']:>2} {item['fn']:>2}"
        )


def recommend_threshold(
    reports: list[dict[str, Any]],
    *,
    weight_frr: float,
    weight_fnr: float,
) -> dict[str, Any]:
    if not reports:
        raise ValueError("No reports to recommend threshold from.")
    best = reports[0]
    best_score = float("inf")
    for item in reports:
        score = (item["false_retrieval_rate"] * weight_frr) + (
            item["missed_retrieval_rate"] * weight_fnr
        )
        if score < best_score:
            best = item
            best_score = score
    return {"report": best, "score": round(best_score, 6)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline evaluator for adaptive retrieval routing decisions."
    )
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path(__file__).with_name("intent_routing_eval_samples.jsonl"),
        help="Path to JSONL dataset. One sample per line.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.75,
        help="Primary confidence threshold for need_retrieval=false to skip retrieval.",
    )
    parser.add_argument(
        "--sweep",
        type=str,
        default="0.60,0.70,0.75,0.80,0.85",
        help="Comma-separated threshold sweep values.",
    )
    parser.add_argument(
        "--plan-kb-id",
        type=int,
        default=1,
        help="Synthetic KB id used to satisfy routing preconditions.",
    )
    parser.add_argument(
        "--show-failures",
        type=int,
        default=5,
        help="Number of top mismatch samples to print for the primary threshold.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full reports in JSON.",
    )
    parser.add_argument(
        "--require-human-verified",
        action="store_true",
        help="Evaluate only lines with `human_verified=true`.",
    )
    parser.add_argument(
        "--weight-frr",
        type=float,
        default=0.6,
        help="Weight for false retrieval rate when recommending threshold.",
    )
    parser.add_argument(
        "--weight-fnr",
        type=float,
        default=0.4,
        help="Weight for missed retrieval rate when recommending threshold.",
    )
    return parser


def main() -> None:
    logging.getLogger("service.core.conversation.routing_decision").setLevel(logging.ERROR)
    parser = build_parser()
    args = parser.parse_args()
    samples, skipped_unverified = load_samples(
        args.samples,
        require_human_verified=bool(args.require_human_verified),
    )
    thresholds = parse_thresholds(args.sweep, fallback=args.threshold)
    retrieval_plan = [("user", int(args.plan_kb_id))]
    reports = [
        evaluate_samples(samples=samples, retrieval_plan=retrieval_plan, threshold=threshold)
        for threshold in thresholds
    ]
    print_table(reports)
    if args.require_human_verified:
        print(
            f"Loaded {len(samples)} human-verified samples "
            f"(skipped_unverified={skipped_unverified})."
        )
    recommendation = recommend_threshold(
        reports,
        weight_frr=max(0.0, float(args.weight_frr)),
        weight_fnr=max(0.0, float(args.weight_fnr)),
    )
    best = recommendation["report"]
    print(
        f"Recommended threshold={best['threshold']:.2f} "
        f"(score={recommendation['score']:.6f}, "
        f"FRR={best['false_retrieval_rate']:.4f}, "
        f"FNR={best['missed_retrieval_rate']:.4f})"
    )

    primary_threshold = coerce_confidence(args.threshold, default=0.75)
    primary = next((item for item in reports if item["threshold"] == primary_threshold), None)
    if primary is None:
        primary = reports[0]

    print("")
    print(
        f"Primary threshold={primary['threshold']:.2f} "
        f"accuracy={primary['accuracy']:.4f} "
        f"FRR={primary['false_retrieval_rate']:.4f} "
        f"FNR={primary['missed_retrieval_rate']:.4f}"
    )

    show_failures = max(0, int(args.show_failures))
    if show_failures > 0 and primary["failures"]:
        print("Top mismatches:")
        for item in primary["failures"][:show_failures]:
            print(
                "- "
                f"expected={item['expected_need_retrieval']} "
                f"predicted={item['predicted_need_retrieval']} "
                f"confidence={item['confidence']:.3f} "
                f"query_type={item['query_type']} "
                f"reason={item['reason']} "
                f"question={item['question']}"
            )

    if args.json:
        print("")
        print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
