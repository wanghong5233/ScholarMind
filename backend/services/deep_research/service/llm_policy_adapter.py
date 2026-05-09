from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)


def _bootstrap_shared_path() -> None:
    here = Path(__file__).resolve()
    candidates = [Path("/shared")]
    for parent in here.parents:
        candidates.append(parent / "shared")
    for candidate in candidates:
        try:
            if candidate.exists() and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
        except OSError:
            continue


_bootstrap_shared_path()

try:
    from llm_policy.resolver import LLMPolicyResolver
except Exception:  # pragma: no cover
    LLMPolicyResolver = None  # type: ignore[assignment]


class _FallbackResolver:
    policy_version = "legacy"
    rollout_steps = (5, 20, 50, 100)

    def refresh(self, *, manifest_path: Optional[str] = None) -> None:
        _ = manifest_path

    def resolve(
        self,
        *,
        task_id: str,
        model_name: str,
        override_max_output_tokens: Optional[int] = None,
        override_temperature: Optional[float] = None,
        override_retries: Optional[int] = None,
        override_timeout_secs: Optional[float] = None,
    ):
        normalized = str(model_name or "").strip().lower()
        token_param = "max_completion_tokens" if normalized.startswith("gpt-5") else "max_tokens"
        supports_custom_temperature = not normalized.startswith("gpt-5")
        default_max_tokens = int(getattr(settings, "REPORT_LLM_MAX_TOKENS", 2560) or 2560)
        max_tokens = int(override_max_output_tokens or default_max_tokens)
        temperature = float(
            override_temperature
            if override_temperature is not None
            else getattr(settings, "REPORT_LLM_TEMPERATURE", 0.2)
        )
        send_temperature = supports_custom_temperature or abs(float(temperature) - 1.0) < 1e-6
        retries = int(override_retries if override_retries is not None else 2)
        timeout_secs = float(override_timeout_secs if override_timeout_secs is not None else getattr(settings, "REQUEST_TIMEOUT", 120))
        return type(
            "ResolvedTaskPolicy",
            (),
            {
                "policy_version": "legacy",
                "task_id": task_id,
                "model_name": model_name,
                "token_param": token_param,
                "supports_custom_temperature": supports_custom_temperature,
                "send_temperature": send_temperature,
                "context_window_hint": 128000,
                "max_output_tokens": max_tokens,
                "temperature": temperature,
                "retries": retries,
                "timeout_secs": timeout_secs,
            },
        )()


_RESOLVER = None


def get_policy_resolver():
    global _RESOLVER
    if _RESOLVER is not None:
        return _RESOLVER
    manifest_path = str(getattr(settings, "LLM_POLICY_MANIFEST_PATH", "") or "").strip() or None
    if bool(getattr(settings, "LLM_POLICY_ENABLED", True)) and LLMPolicyResolver is not None:
        try:
            _RESOLVER = LLMPolicyResolver(manifest_path=manifest_path)
            return _RESOLVER
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to initialize shared LLM policy resolver: %s", exc)
    _RESOLVER = _FallbackResolver()
    return _RESOLVER
