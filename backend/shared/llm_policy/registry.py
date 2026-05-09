from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .types import (
    ModelCapabilityProfile,
    PolicyManifest,
    TaskPolicy,
    clamp_float,
    clamp_int,
    ensure_rollout_steps,
)

logger = logging.getLogger(__name__)


def default_manifest_payload() -> dict[str, Any]:
    return {
        "policy_version": "v1",
        "default_task": "app.answer",
        "rollout": {"recommended_steps": [5, 20, 50, 100]},
        "model_capabilities": [
            {
                "profile_id": "openai_gpt5",
                "model_prefixes": ["gpt-5"],
                "token_param": "max_completion_tokens",
                "supports_custom_temperature": False,
                "context_window_hint": 400000,
            },
            {
                "profile_id": "openai_gpt41",
                "model_prefixes": ["gpt-4.1"],
                "token_param": "max_tokens",
                "supports_custom_temperature": True,
                "context_window_hint": 1048576,
            },
            {
                "profile_id": "openai_gpt4o",
                "model_prefixes": ["gpt-4o"],
                "token_param": "max_tokens",
                "supports_custom_temperature": True,
                "context_window_hint": 128000,
            },
            {
                "profile_id": "openai_o_series",
                "model_prefixes": ["o1", "o3", "o4"],
                "token_param": "max_tokens",
                "supports_custom_temperature": True,
                "context_window_hint": 200000,
            },
            {
                "profile_id": "dashscope_vl",
                "model_prefixes": ["qwen-vl"],
                "token_param": "max_tokens",
                "supports_custom_temperature": True,
                "context_window_hint": 32000,
            },
            {
                "profile_id": "dashscope_text",
                "model_prefixes": ["qwen", "deepseek"],
                "token_param": "max_tokens",
                "supports_custom_temperature": True,
                "context_window_hint": 200000,
            },
            {
                "profile_id": "fallback_default",
                "model_prefixes": [],
                "token_param": "max_tokens",
                "supports_custom_temperature": True,
                "context_window_hint": 128000,
            },
        ],
        "task_policies": {
            "app.answer": {
                "default_max_output_tokens": 3072,
                "min_output_tokens": 256,
                "max_output_tokens": 8192,
                "default_temperature": 0.3,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.aux": {
                "default_max_output_tokens": 512,
                "min_output_tokens": 128,
                "max_output_tokens": 2048,
                "default_temperature": 0.0,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.summary": {
                "default_max_output_tokens": 256,
                "min_output_tokens": 128,
                "max_output_tokens": 1024,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.compression": {
                "default_max_output_tokens": 1024,
                "min_output_tokens": 256,
                "max_output_tokens": 2048,
                "default_temperature": 0.0,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.rewrite": {
                "default_max_output_tokens": 256,
                "min_output_tokens": 128,
                "max_output_tokens": 512,
                "default_temperature": 0.1,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.translate": {
                "default_max_output_tokens": 256,
                "min_output_tokens": 128,
                "max_output_tokens": 512,
                "default_temperature": 0.0,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.hyde": {
                "default_max_output_tokens": 256,
                "min_output_tokens": 128,
                "max_output_tokens": 1024,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.graph": {
                "default_max_output_tokens": 512,
                "min_output_tokens": 128,
                "max_output_tokens": 1024,
                "default_temperature": 0.1,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.fact_extraction": {
                "default_max_output_tokens": 512,
                "min_output_tokens": 128,
                "max_output_tokens": 1024,
                "default_temperature": 0.1,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "app.equation_description": {
                "default_max_output_tokens": 2048,
                "min_output_tokens": 256,
                "max_output_tokens": 3072,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 60,
            },
            "docstudio.ask": {
                "default_max_output_tokens": 1200,
                "min_output_tokens": 256,
                "max_output_tokens": 1600,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 75,
            },
            "docstudio.guardrail": {
                "default_max_output_tokens": 520,
                "min_output_tokens": 256,
                "max_output_tokens": 800,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 0.3,
                "default_retries": 2,
                "default_timeout_secs": 75,
            },
            "docstudio.analysis": {
                "default_max_output_tokens": 2000,
                "min_output_tokens": 512,
                "max_output_tokens": 2400,
                "default_temperature": 0.3,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 75,
            },
            "docstudio.answer_without_edit": {
                "default_max_output_tokens": 800,
                "min_output_tokens": 256,
                "max_output_tokens": 1200,
                "default_temperature": 0.3,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 75,
            },
            "deepresearch.rag_summary": {
                "default_max_output_tokens": 512,
                "min_output_tokens": 256,
                "max_output_tokens": 1024,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 120,
            },
            "deepresearch.decision": {
                "default_max_output_tokens": 768,
                "min_output_tokens": 256,
                "max_output_tokens": 2048,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 120,
            },
            "deepresearch.report": {
                "default_max_output_tokens": 2560,
                "min_output_tokens": 512,
                "max_output_tokens": 4096,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 120,
            },
            "deepresearch.report_section": {
                "default_max_output_tokens": 1280,
                "min_output_tokens": 512,
                "max_output_tokens": 2048,
                "default_temperature": 0.2,
                "min_temperature": 0.0,
                "max_temperature": 1.0,
                "default_retries": 2,
                "default_timeout_secs": 120,
            },
        },
    }


def _coerce_int(value: Any, *, fallback: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(fallback)
    return clamp_int(parsed, low=low, high=high)


def _coerce_float(value: Any, *, fallback: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(fallback)
    return clamp_float(parsed, low=low, high=high)


def build_manifest(payload: dict[str, Any]) -> PolicyManifest:
    raw_tasks = payload.get("task_policies") or {}
    raw_caps = payload.get("model_capabilities") or []
    raw_rollout = (payload.get("rollout") or {}).get("recommended_steps") or []

    model_caps = []
    for item in raw_caps:
        if not isinstance(item, dict):
            continue
        prefixes = item.get("model_prefixes") or []
        model_caps.append(
            ModelCapabilityProfile(
                profile_id=str(item.get("profile_id") or "unknown"),
                model_prefixes=tuple(str(prefix).strip().lower() for prefix in prefixes if str(prefix).strip()),
                token_param=(
                    "max_completion_tokens"
                    if str(item.get("token_param") or "").strip() == "max_completion_tokens"
                    else "max_tokens"
                ),
                supports_custom_temperature=bool(item.get("supports_custom_temperature", True)),
                context_window_hint=_coerce_int(
                    item.get("context_window_hint"),
                    fallback=128000,
                    low=4096,
                    high=4000000,
                ),
            )
        )
    if not model_caps:
        model_caps = build_manifest(default_manifest_payload()).model_capabilities

    task_policies: dict[str, TaskPolicy] = {}
    for task_id, item in raw_tasks.items():
        if not isinstance(item, dict):
            continue
        task_key = str(task_id or "").strip()
        if not task_key:
            continue
        min_output = _coerce_int(item.get("min_output_tokens"), fallback=128, low=1, high=100000)
        max_output = _coerce_int(item.get("max_output_tokens"), fallback=4096, low=min_output, high=100000)
        default_output = _coerce_int(
            item.get("default_max_output_tokens"),
            fallback=min(max_output, 1024),
            low=min_output,
            high=max_output,
        )
        min_temp = _coerce_float(item.get("min_temperature"), fallback=0.0, low=0.0, high=2.0)
        max_temp = _coerce_float(item.get("max_temperature"), fallback=1.0, low=min_temp, high=2.0)
        default_temp = _coerce_float(
            item.get("default_temperature"),
            fallback=min(max_temp, 0.3),
            low=min_temp,
            high=max_temp,
        )
        task_policies[task_key] = TaskPolicy(
            task_id=task_key,
            default_max_output_tokens=default_output,
            min_output_tokens=min_output,
            max_output_tokens=max_output,
            default_temperature=default_temp,
            min_temperature=min_temp,
            max_temperature=max_temp,
            default_retries=_coerce_int(item.get("default_retries"), fallback=2, low=0, high=10),
            default_timeout_secs=_coerce_float(item.get("default_timeout_secs"), fallback=60.0, low=1.0, high=600.0),
        )

    if not task_policies:
        task_policies = build_manifest(default_manifest_payload()).task_policies

    default_task = str(payload.get("default_task") or "").strip()
    if default_task not in task_policies:
        default_task = next(iter(task_policies.keys()))

    return PolicyManifest(
        policy_version=str(payload.get("policy_version") or "v1"),
        default_task=default_task,
        model_capabilities=tuple(model_caps),
        task_policies=task_policies,
        rollout_steps=ensure_rollout_steps([_coerce_int(item, fallback=0, low=0, high=100) for item in raw_rollout]),
    )


def load_manifest(manifest_path: str | None = None) -> PolicyManifest:
    payload = default_manifest_payload()
    candidate = str(manifest_path or "").strip()
    if candidate:
        manifest_file = Path(candidate)
        if manifest_file.exists():
            try:
                payload = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("Failed to read policy manifest %s: %s", manifest_file, exc)
        else:
            logger.warning("Policy manifest not found: %s; fallback to built-in defaults.", manifest_file)
    return build_manifest(payload)
