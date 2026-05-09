from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
APP_SCRIPTS = APP_ROOT / "scripts"
os.chdir(APP_ROOT)
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(APP_SCRIPTS))

os.environ.setdefault("DATABASE_URL", "sqlite:///./routing_eval_ci.db")

from eval_intent_routing import (  # noqa: E402
    coerce_confidence,
    evaluate_samples,
    load_samples,
    parse_thresholds,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CI guardrail for adaptive-retrieval FRR/FNR thresholds.",
    )
    parser.add_argument(
        "--samples",
        type=Path,
        default=APP_SCRIPTS / "intent_routing_eval_samples.jsonl",
        help="Path to routing-eval samples JSONL.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.75,
        help="Primary threshold used for gating.",
    )
    parser.add_argument(
        "--sweep",
        type=str,
        default="0.60,0.70,0.75,0.80,0.85",
        help="Optional sweep to print additional diagnostics.",
    )
    parser.add_argument(
        "--plan-kb-id",
        type=int,
        default=1,
        help="Synthetic kb id used by evaluator.",
    )
    parser.add_argument(
        "--max-frr",
        type=float,
        default=0.25,
        help="Max allowed false retrieval rate.",
    )
    parser.add_argument(
        "--max-fnr",
        type=float,
        default=0.25,
        help="Max allowed missed retrieval rate.",
    )
    parser.add_argument(
        "--require-human-verified",
        action="store_true",
        help="Evaluate only human_verified=true rows.",
    )
    parser.add_argument(
        "--strict-provider-check",
        action="store_true",
        help="Fail when OPENAI/DASHSCOPE API key is missing.",
    )
    return parser


def _has_provider_key() -> bool:
    return bool(
        str(os.getenv("OPENAI_API_KEY") or "").strip()
        or str(os.getenv("DASHSCOPE_API_KEY") or "").strip()
    )


def main() -> None:
    logging.getLogger("service.core.conversation.routing_decision").setLevel(logging.ERROR)
    args = build_parser().parse_args()

    if not _has_provider_key():
        message = "Routing threshold check skipped: OPENAI_API_KEY/DASHSCOPE_API_KEY not configured."
        if args.strict_provider_check:
            raise SystemExit(message)
        print(message)
        return

    samples, skipped_unverified = load_samples(
        args.samples,
        require_human_verified=bool(args.require_human_verified),
    )
    retrieval_plan = [("user", int(args.plan_kb_id))]
    thresholds = parse_thresholds(args.sweep, fallback=args.threshold)
    reports = [
        evaluate_samples(
            samples=samples,
            retrieval_plan=retrieval_plan,
            threshold=threshold,
        )
        for threshold in thresholds
    ]
    primary_threshold = coerce_confidence(args.threshold, default=0.75)
    primary = next((item for item in reports if item["threshold"] == primary_threshold), reports[0])

    print(
        "Routing CI gate: "
        f"threshold={primary['threshold']:.2f} "
        f"FRR={primary['false_retrieval_rate']:.4f} "
        f"FNR={primary['missed_retrieval_rate']:.4f} "
        f"accuracy={primary['accuracy']:.4f} "
        f"samples={len(samples)} "
        f"skipped_unverified={skipped_unverified}"
    )

    failed = False
    if float(primary["false_retrieval_rate"]) > float(args.max_frr):
        print(
            f"FRR gate failed: {primary['false_retrieval_rate']:.4f} > max_frr={float(args.max_frr):.4f}"
        )
        failed = True
    if float(primary["missed_retrieval_rate"]) > float(args.max_fnr):
        print(
            f"FNR gate failed: {primary['missed_retrieval_rate']:.4f} > max_fnr={float(args.max_fnr):.4f}"
        )
        failed = True

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
