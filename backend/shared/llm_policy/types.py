from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class ModelCapabilityProfile:
    """Capability descriptor for one model family."""

    profile_id: str
    model_prefixes: tuple[str, ...]
    token_param: str
    supports_custom_temperature: bool
    context_window_hint: int

    def matches(self, model_name: str) -> bool:
        normalized = str(model_name or "").strip().lower()
        if not normalized:
            return False
        return any(normalized.startswith(prefix) for prefix in self.model_prefixes)


@dataclass(frozen=True)
class TaskPolicy:
    """Task-level generation policy."""

    task_id: str
    default_max_output_tokens: int
    min_output_tokens: int
    max_output_tokens: int
    default_temperature: float
    min_temperature: float
    max_temperature: float
    default_retries: int
    default_timeout_secs: float


@dataclass(frozen=True)
class PolicyManifest:
    """Runtime policy manifest loaded from JSON."""

    policy_version: str
    default_task: str
    model_capabilities: tuple[ModelCapabilityProfile, ...]
    task_policies: Dict[str, TaskPolicy]
    rollout_steps: tuple[int, ...]

    def find_model_profile(self, model_name: str) -> ModelCapabilityProfile:
        for profile in self.model_capabilities:
            if profile.matches(model_name):
                return profile
        return self.model_capabilities[-1]

    def find_task_policy(self, task_id: str) -> TaskPolicy:
        normalized = str(task_id or "").strip()
        if normalized and normalized in self.task_policies:
            return self.task_policies[normalized]
        if self.default_task in self.task_policies:
            return self.task_policies[self.default_task]
        if self.task_policies:
            first_key = next(iter(self.task_policies.keys()))
            return self.task_policies[first_key]
        raise KeyError("No task policy configured in manifest.")


@dataclass(frozen=True)
class ResolvedTaskPolicy:
    """Task policy after applying model capability and overrides."""

    policy_version: str
    task_id: str
    model_name: str
    token_param: str
    supports_custom_temperature: bool
    send_temperature: bool
    context_window_hint: int
    max_output_tokens: int
    temperature: float
    retries: int
    timeout_secs: float


def clamp_int(value: int, *, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def clamp_float(value: float, *, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def ensure_rollout_steps(values: List[int]) -> tuple[int, ...]:
    normalized = []
    for item in values:
        point = clamp_int(item, low=0, high=100)
        if point not in normalized:
            normalized.append(point)
    if not normalized:
        return (5, 20, 50, 100)
    return tuple(normalized)
