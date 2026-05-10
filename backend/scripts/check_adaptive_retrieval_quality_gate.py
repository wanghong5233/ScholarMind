from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Optional


BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
APP_SCRIPTS = APP_ROOT / "scripts"

os.chdir(APP_ROOT)
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(APP_SCRIPTS))

os.environ.setdefault("DATABASE_URL", "sqlite:///./routing_quality_gate.db")

from eval_intent_routing import (  # noqa: E402
    coerce_confidence,
    evaluate_samples,
    load_samples,
)
from core.config import settings as app_settings  # noqa: E402
from report_route_metrics import (  # noqa: E402
    build_report,
    iter_events,
)


@dataclass(frozen=True)
class OfflineGatePolicy:
    threshold: float
    max_false_retrieval_rate: float
    max_missed_retrieval_rate: float
    min_accuracy: float
    min_query_type_accuracy: float
    min_samples: int
    require_human_verified: bool
    strict_provider_check: bool


@dataclass(frozen=True)
class OnlineGatePolicy:
    min_events: int
    max_p95_total_ms: Optional[float]
    max_p95_retrieval_ms: Optional[float]
    max_p95_generation_ms: Optional[float]
    max_legacy_missing_route_ratio: Optional[float]


@dataclass(frozen=True)
class RolloutPolicy:
    steps: tuple[int, ...]
    rollback_percent: int
    promote_on_pass: bool
    fail_on_violation: bool


@dataclass(frozen=True)
class QualityGatePolicy:
    policy_version: str
    offline_eval: OfflineGatePolicy
    online_metrics: OnlineGatePolicy
    rollout: RolloutPolicy


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    actual: float | int | str
    expected: str
    message: str


@dataclass(frozen=True)
class GateResult:
    status: str
    checks: tuple[GateCheck, ...] = field(default_factory=tuple)
    details: dict[str, Any] = field(default_factory=dict)


def _coerce_float(
    payload: dict[str, Any],
    key: str,
    *,
    default: float,
    low: float,
    high: float,
) -> float:
    value = payload.get(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid float for '{key}': {value!r}") from exc
    return max(low, min(high, parsed))


def _coerce_int(
    payload: dict[str, Any],
    key: str,
    *,
    default: int,
    low: int,
    high: int,
) -> int:
    value = payload.get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid int for '{key}': {value!r}") from exc
    return max(low, min(high, parsed))


def _coerce_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return default


def _coerce_optional_float(
    payload: dict[str, Any],
    key: str,
    *,
    default: Optional[float],
    low: float,
    high: float,
) -> Optional[float]:
    value = payload.get(key, default)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid optional float for '{key}': {value!r}") from exc
    return max(low, min(high, parsed))


def _normalize_steps(values: Any) -> tuple[int, ...]:
    if not isinstance(values, list):
        return (5, 20, 50, 100)
    normalized: list[int] = []
    for item in values:
        try:
            step = int(item)
        except (TypeError, ValueError):
            continue
        step = max(0, min(100, step))
        if step not in normalized:
            normalized.append(step)
    if not normalized:
        return (5, 20, 50, 100)
    return tuple(sorted(normalized))


def load_policy(path: Path) -> QualityGatePolicy:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read policy file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON policy file: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Policy payload must be object: {path}")

    offline_raw = payload.get("offline_eval")
    online_raw = payload.get("online_metrics")
    rollout_raw = payload.get("rollout")
    if not isinstance(offline_raw, dict):
        raise ValueError("Policy missing object field: offline_eval")
    if not isinstance(online_raw, dict):
        raise ValueError("Policy missing object field: online_metrics")
    if not isinstance(rollout_raw, dict):
        raise ValueError("Policy missing object field: rollout")

    offline = OfflineGatePolicy(
        threshold=coerce_confidence(
            _coerce_float(offline_raw, "threshold", default=0.75, low=0.0, high=1.0),
            default=0.75,
        ),
        max_false_retrieval_rate=_coerce_float(
            offline_raw, "max_false_retrieval_rate", default=0.25, low=0.0, high=1.0
        ),
        max_missed_retrieval_rate=_coerce_float(
            offline_raw, "max_missed_retrieval_rate", default=0.25, low=0.0, high=1.0
        ),
        min_accuracy=_coerce_float(offline_raw, "min_accuracy", default=0.75, low=0.0, high=1.0),
        min_query_type_accuracy=_coerce_float(
            offline_raw, "min_query_type_accuracy", default=0.0, low=0.0, high=1.0
        ),
        min_samples=_coerce_int(offline_raw, "min_samples", default=30, low=1, high=100000),
        require_human_verified=_coerce_bool(
            offline_raw, "require_human_verified", default=False
        ),
        strict_provider_check=_coerce_bool(
            offline_raw, "strict_provider_check", default=False
        ),
    )
    online = OnlineGatePolicy(
        min_events=_coerce_int(online_raw, "min_events", default=20, low=0, high=1000000),
        max_p95_total_ms=_coerce_optional_float(
            online_raw, "max_p95_total_ms", default=None, low=0.0, high=10_000_000.0
        ),
        max_p95_retrieval_ms=_coerce_optional_float(
            online_raw, "max_p95_retrieval_ms", default=None, low=0.0, high=10_000_000.0
        ),
        max_p95_generation_ms=_coerce_optional_float(
            online_raw, "max_p95_generation_ms", default=None, low=0.0, high=10_000_000.0
        ),
        max_legacy_missing_route_ratio=_coerce_optional_float(
            online_raw, "max_legacy_missing_route_ratio", default=None, low=0.0, high=1.0
        ),
    )
    rollout = RolloutPolicy(
        steps=_normalize_steps(rollout_raw.get("steps")),
        rollback_percent=_coerce_int(rollout_raw, "rollback_percent", default=5, low=0, high=100),
        promote_on_pass=_coerce_bool(rollout_raw, "promote_on_pass", default=False),
        fail_on_violation=_coerce_bool(rollout_raw, "fail_on_violation", default=True),
    )
    policy_version = str(payload.get("policy_version") or "v1").strip() or "v1"
    return QualityGatePolicy(
        policy_version=policy_version,
        offline_eval=offline,
        online_metrics=online,
        rollout=rollout,
    )


def _has_provider_key() -> bool:
    return bool(
        str(os.getenv("OPENAI_API_KEY") or "").strip()
        or str(os.getenv("DASHSCOPE_API_KEY") or "").strip()
        or str(getattr(app_settings, "OPENAI_API_KEY", "") or "").strip()
        or str(getattr(app_settings, "DASHSCOPE_API_KEY", "") or "").strip()
    )


def _resolve_logs_dir(explicit_logs_dir: Optional[Path], glob_pattern: str) -> tuple[Optional[Path], int]:
    candidates: list[Path] = []
    if explicit_logs_dir is not None:
        candidates.append(explicit_logs_dir)
    candidates.extend(
        [
            Path("/app/logs"),
            BACKEND_ROOT / "logs",
            APP_ROOT / "logs",
        ]
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        files = list(candidate.glob(glob_pattern))
        if files:
            return candidate, len(files)
    return None, 0


def _resolve_user_path(path_value: Path, *, base_dir: Path) -> Path:
    if path_value.is_absolute():
        return path_value
    return (base_dir / path_value).resolve()


def run_offline_gate(
    *,
    policy: OfflineGatePolicy,
    samples_path: Path,
    plan_kb_id: int,
) -> GateResult:
    if not _has_provider_key():
        if policy.strict_provider_check:
            check = GateCheck(
                name="provider_key",
                passed=False,
                actual="missing",
                expected="OPENAI_API_KEY or DASHSCOPE_API_KEY configured",
                message="Provider key missing under strict_provider_check.",
            )
            return GateResult(status="failed", checks=(check,), details={})
        return GateResult(
            status="skipped",
            checks=(),
            details={
                "reason": "provider_key_missing",
                "strict_provider_check": False,
            },
        )

    samples, skipped_unverified = load_samples(
        samples_path,
        require_human_verified=policy.require_human_verified,
    )
    report = evaluate_samples(
        samples=samples,
        retrieval_plan=[("user", int(plan_kb_id))],
        threshold=policy.threshold,
    )
    checks = (
        GateCheck(
            name="sample_count",
            passed=len(samples) >= policy.min_samples,
            actual=len(samples),
            expected=f">= {policy.min_samples}",
            message="Offline labeled sample volume",
        ),
        GateCheck(
            name="false_retrieval_rate",
            passed=float(report["false_retrieval_rate"]) <= policy.max_false_retrieval_rate,
            actual=float(report["false_retrieval_rate"]),
            expected=f"<= {policy.max_false_retrieval_rate:.4f}",
            message="False retrieval rate must stay under redline",
        ),
        GateCheck(
            name="missed_retrieval_rate",
            passed=float(report["missed_retrieval_rate"]) <= policy.max_missed_retrieval_rate,
            actual=float(report["missed_retrieval_rate"]),
            expected=f"<= {policy.max_missed_retrieval_rate:.4f}",
            message="Missed retrieval rate must stay under redline",
        ),
        GateCheck(
            name="accuracy",
            passed=float(report["accuracy"]) >= policy.min_accuracy,
            actual=float(report["accuracy"]),
            expected=f">= {policy.min_accuracy:.4f}",
            message="Overall routing accuracy must meet baseline",
        ),
        GateCheck(
            name="query_type_accuracy",
            passed=float(report["query_type_accuracy"]) >= policy.min_query_type_accuracy,
            actual=float(report["query_type_accuracy"]),
            expected=f">= {policy.min_query_type_accuracy:.4f}",
            message="Query-type routing consistency",
        ),
    )
    failed = any(not item.passed for item in checks)
    return GateResult(
        status="failed" if failed else "passed",
        checks=checks,
        details={
            "threshold": policy.threshold,
            "samples": len(samples),
            "skipped_unverified": skipped_unverified,
            "report": report,
        },
    )


def run_online_gate(
    *,
    policy: OnlineGatePolicy,
    logs_dir: Optional[Path],
    glob_pattern: str,
    start_ts: int,
    end_ts: int,
) -> GateResult:
    resolved_logs_dir, file_count = _resolve_logs_dir(logs_dir, glob_pattern)
    if resolved_logs_dir is None:
        return GateResult(
            status="skipped",
            checks=(),
            details={
                "reason": "no_log_files",
                "glob": glob_pattern,
            },
        )

    events = list(
        iter_events(
            logs_dir=resolved_logs_dir,
            glob_pattern=glob_pattern,
            start_ts=max(0, int(start_ts)),
            end_ts=max(0, int(end_ts)),
        )
    )
    report = build_report(events)
    total = int(report.get("total") or 0)
    timing = report.get("timing") if isinstance(report.get("timing"), dict) else {}
    total_ms = timing.get("total_ms") if isinstance(timing.get("total_ms"), dict) else {}
    retrieval_ms = (
        timing.get("retrieval_ms") if isinstance(timing.get("retrieval_ms"), dict) else {}
    )
    generation_ms = (
        timing.get("generation_ms") if isinstance(timing.get("generation_ms"), dict) else {}
    )
    route_reason_distribution = (
        report.get("route_reason_distribution")
        if isinstance(report.get("route_reason_distribution"), dict)
        else {}
    )
    legacy_missing_route = int(route_reason_distribution.get("legacy_missing_route") or 0)
    legacy_ratio = (legacy_missing_route / total) if total > 0 else 0.0

    checks: list[GateCheck] = [
        GateCheck(
            name="event_count",
            passed=total >= policy.min_events,
            actual=total,
            expected=f">= {policy.min_events}",
            message="Online traffic sample volume",
        )
    ]
    if policy.max_p95_total_ms is not None:
        checks.append(
            GateCheck(
                name="p95_total_ms",
                passed=float(total_ms.get("p95") or 0.0) <= policy.max_p95_total_ms,
                actual=float(total_ms.get("p95") or 0.0),
                expected=f"<= {policy.max_p95_total_ms:.2f}",
                message="End-to-end p95 latency",
            )
        )
    if policy.max_p95_retrieval_ms is not None:
        checks.append(
            GateCheck(
                name="p95_retrieval_ms",
                passed=float(retrieval_ms.get("p95") or 0.0) <= policy.max_p95_retrieval_ms,
                actual=float(retrieval_ms.get("p95") or 0.0),
                expected=f"<= {policy.max_p95_retrieval_ms:.2f}",
                message="Retrieval-stage p95 latency",
            )
        )
    if policy.max_p95_generation_ms is not None:
        checks.append(
            GateCheck(
                name="p95_generation_ms",
                passed=float(generation_ms.get("p95") or 0.0) <= policy.max_p95_generation_ms,
                actual=float(generation_ms.get("p95") or 0.0),
                expected=f"<= {policy.max_p95_generation_ms:.2f}",
                message="Generation-stage p95 latency",
            )
        )
    if policy.max_legacy_missing_route_ratio is not None:
        checks.append(
            GateCheck(
                name="legacy_missing_route_ratio",
                passed=legacy_ratio <= policy.max_legacy_missing_route_ratio,
                actual=round(legacy_ratio, 6),
                expected=f"<= {policy.max_legacy_missing_route_ratio:.6f}",
                message="Legacy route fallback ratio must remain near zero",
            )
        )

    failed = any(not item.passed for item in checks)
    return GateResult(
        status="failed" if failed else "passed",
        checks=tuple(checks),
        details={
            "logs_dir": str(resolved_logs_dir),
            "log_files": file_count,
            "events": total,
            "report": report,
        },
    )


def _next_rollout_step(current_rollout: int, steps: tuple[int, ...]) -> int:
    normalized_current = max(0, min(100, int(current_rollout)))
    for step in steps:
        if step > normalized_current:
            return step
    return normalized_current


def decide_rollout(
    *,
    policy: RolloutPolicy,
    current_rollout: int,
    offline_result: GateResult,
    online_result: GateResult,
) -> dict[str, Any]:
    has_failure = offline_result.status == "failed" or online_result.status == "failed"
    has_skip = offline_result.status == "skipped" or online_result.status == "skipped"
    if has_failure:
        return {
            "status": "failed",
            "action": "rollback",
            "recommended_rollout_percent": int(policy.rollback_percent),
            "reason": "quality_gate_failed",
        }
    if has_skip:
        return {
            "status": "insufficient_signal",
            "action": "hold",
            "recommended_rollout_percent": max(0, min(100, int(current_rollout))),
            "reason": "insufficient_observability_signal",
        }
    if policy.promote_on_pass:
        next_rollout = _next_rollout_step(current_rollout, policy.steps)
        action = "promote" if next_rollout > int(current_rollout) else "hold"
        return {
            "status": "passed",
            "action": action,
            "recommended_rollout_percent": int(next_rollout),
            "reason": "all_quality_gates_passed",
        }
    return {
        "status": "passed",
        "action": "hold",
        "recommended_rollout_percent": max(0, min(100, int(current_rollout))),
        "reason": "all_quality_gates_passed",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quality-first release gate for adaptive retrieval rollout.",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=BACKEND_ROOT / "scripts" / "adaptive_retrieval_quality_policy.v1.json",
        help="Path to quality policy JSON.",
    )
    parser.add_argument(
        "--samples",
        type=Path,
        default=APP_SCRIPTS / "intent_routing_eval_samples.jsonl",
        help="Offline routing-eval sample JSONL path.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help="Optional logs directory containing ask_events.*.jsonl.",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="ask_events.*.jsonl",
        help="Glob for route events logs.",
    )
    parser.add_argument(
        "--start-ts",
        type=int,
        default=0,
        help="Window start timestamp (ms, inclusive).",
    )
    parser.add_argument(
        "--end-ts",
        type=int,
        default=0,
        help="Window end timestamp (ms, inclusive). 0 means no upper bound.",
    )
    parser.add_argument(
        "--lookback-minutes",
        type=int,
        default=180,
        help="When --start-ts is 0, auto-use now-lookback window. Set 0 to disable.",
    )
    parser.add_argument(
        "--plan-kb-id",
        type=int,
        default=1,
        help="Synthetic kb id used by offline routing evaluator.",
    )
    parser.add_argument(
        "--current-rollout",
        type=int,
        default=int(os.getenv("SM_ADAPTIVE_RETRIEVAL_ROLLOUT_PERCENT", "100")),
        help="Current rollout percent used by decision engine.",
    )
    parser.add_argument(
        "--strict-provider-check",
        action="store_true",
        help="Force offline gate failure when provider keys are missing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON result.",
    )
    return parser


def _serialize_gate_result(result: GateResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "checks": [asdict(item) for item in result.checks],
        "details": result.details,
    }


def main() -> None:
    args = build_parser().parse_args()
    policy_path = _resolve_user_path(args.policy, base_dir=BACKEND_ROOT)
    samples_path = _resolve_user_path(args.samples, base_dir=BACKEND_ROOT)
    logs_dir = (
        _resolve_user_path(args.logs_dir, base_dir=BACKEND_ROOT)
        if args.logs_dir is not None
        else None
    )
    policy = load_policy(policy_path)
    offline_policy = policy.offline_eval
    if bool(args.strict_provider_check):
        offline_policy = replace(offline_policy, strict_provider_check=True)

    start_ts = max(0, int(args.start_ts))
    if start_ts <= 0 and int(args.lookback_minutes) > 0:
        lookback_ms = max(0, int(args.lookback_minutes)) * 60 * 1000
        start_ts = max(0, int(time.time() * 1000) - lookback_ms)
    end_ts = max(0, int(args.end_ts))

    offline_result = run_offline_gate(
        policy=offline_policy,
        samples_path=samples_path,
        plan_kb_id=max(1, int(args.plan_kb_id)),
    )
    online_result = run_online_gate(
        policy=policy.online_metrics,
        logs_dir=logs_dir,
        glob_pattern=str(args.glob or "ask_events.*.jsonl"),
        start_ts=start_ts,
        end_ts=end_ts,
    )
    decision = decide_rollout(
        policy=policy.rollout,
        current_rollout=max(0, min(100, int(args.current_rollout))),
        offline_result=offline_result,
        online_result=online_result,
    )

    output = {
        "policy_version": policy.policy_version,
        "current_rollout_percent": max(0, min(100, int(args.current_rollout))),
        "window": {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "lookback_minutes": max(0, int(args.lookback_minutes)),
        },
        "offline_gate": _serialize_gate_result(offline_result),
        "online_gate": _serialize_gate_result(online_result),
        "decision": decision,
    }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"Policy: {policy.policy_version}")
        print(
            f"Offline gate: {offline_result.status} "
            f"(checks={sum(1 for item in offline_result.checks if item.passed)}/{len(offline_result.checks)})"
        )
        for check in offline_result.checks:
            flag = "PASS" if check.passed else "FAIL"
            print(
                f"  - [{flag}] {check.name}: actual={check.actual} expected={check.expected} ({check.message})"
            )
        if offline_result.status == "skipped":
            print(f"  - [SKIP] reason={offline_result.details.get('reason')}")

        print(
            f"Online gate: {online_result.status} "
            f"(checks={sum(1 for item in online_result.checks if item.passed)}/{len(online_result.checks)})"
        )
        for check in online_result.checks:
            flag = "PASS" if check.passed else "FAIL"
            print(
                f"  - [{flag}] {check.name}: actual={check.actual} expected={check.expected} ({check.message})"
            )
        if online_result.status == "skipped":
            print(f"  - [SKIP] reason={online_result.details.get('reason')}")

        print(
            "Decision: "
            f"status={decision['status']} action={decision['action']} "
            f"recommended_rollout_percent={decision['recommended_rollout_percent']} "
            f"reason={decision['reason']}"
        )

    if policy.rollout.fail_on_violation and decision["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
