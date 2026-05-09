from __future__ import annotations

from typing import Optional

from .registry import load_manifest
from .types import ResolvedTaskPolicy, clamp_float, clamp_int


class LLMPolicyResolver:
    """Resolve task parameters against model capability profiles."""

    def __init__(self, *, manifest_path: Optional[str] = None) -> None:
        self._manifest_path = str(manifest_path or "").strip() or None
        self._manifest = load_manifest(self._manifest_path)

    @property
    def policy_version(self) -> str:
        return self._manifest.policy_version

    @property
    def rollout_steps(self) -> tuple[int, ...]:
        return self._manifest.rollout_steps

    def refresh(self, *, manifest_path: Optional[str] = None) -> None:
        if manifest_path is not None:
            self._manifest_path = str(manifest_path or "").strip() or None
        self._manifest = load_manifest(self._manifest_path)

    def resolve(
        self,
        *,
        task_id: str,
        model_name: str,
        override_max_output_tokens: Optional[int] = None,
        override_temperature: Optional[float] = None,
        override_retries: Optional[int] = None,
        override_timeout_secs: Optional[float] = None,
    ) -> ResolvedTaskPolicy:
        task = self._manifest.find_task_policy(task_id)
        model = str(model_name or "").strip()
        profile = self._manifest.find_model_profile(model)

        max_output_tokens = task.default_max_output_tokens
        if override_max_output_tokens is not None:
            max_output_tokens = clamp_int(
                int(override_max_output_tokens),
                low=task.min_output_tokens,
                high=task.max_output_tokens,
            )

        temperature = task.default_temperature
        if override_temperature is not None:
            temperature = clamp_float(
                float(override_temperature),
                low=task.min_temperature,
                high=task.max_temperature,
            )

        retries = task.default_retries if override_retries is None else clamp_int(int(override_retries), low=0, high=10)
        timeout_secs = (
            task.default_timeout_secs
            if override_timeout_secs is None
            else clamp_float(float(override_timeout_secs), low=1.0, high=600.0)
        )

        send_temperature = profile.supports_custom_temperature or abs(float(temperature) - 1.0) < 1e-6
        return ResolvedTaskPolicy(
            policy_version=self._manifest.policy_version,
            task_id=task.task_id,
            model_name=model,
            token_param=profile.token_param,
            supports_custom_temperature=profile.supports_custom_temperature,
            send_temperature=send_temperature,
            context_window_hint=profile.context_window_hint,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            retries=retries,
            timeout_secs=timeout_secs,
        )

    @staticmethod
    def infer_provider_from_model(model_name: Optional[str]) -> Optional[str]:
        normalized = str(model_name or "").strip().lower()
        if not normalized:
            return None
        if normalized.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        if normalized.startswith(("qwen", "deepseek")):
            return "dashscope"
        return None
