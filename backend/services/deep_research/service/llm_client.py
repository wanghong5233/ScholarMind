"""Lightweight LLM client wrapper with provider/model failover."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import logging

from openai import AsyncOpenAI

from core.config import settings
from service.llm_policy_adapter import get_policy_resolver


def _is_placeholder(value: Optional[str]) -> bool:
    """Return True when an API key value is empty or placeholder-like."""

    if not value:
        return True
    raw = value.strip()
    if raw in {"sk-...", "sk-xxxx", "sk-XXXXX"}:
        return True
    return raw.endswith("...")


def _normalize_provider(value: Optional[str], *, default: str = "dashscope") -> str:
    """Normalize provider name to one of supported values."""

    normalized = str(value or "").strip().lower()
    if normalized in {"dashscope", "openai"}:
        return normalized
    return default


def _split_csv_models(value: Optional[str]) -> List[str]:
    """Split CSV model list and drop empty values."""

    items: List[str] = []
    for raw in str(value or "").split(","):
        model = raw.strip()
        if model:
            items.append(model)
    return items


def _dedupe_keep_order(items: List[str]) -> List[str]:
    """Dedupe while preserving order."""

    seen: set[str] = set()
    deduped: List[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


@dataclass(frozen=True)
class LLMEndpoint:
    """Resolved endpoint config for one provider/model pair."""

    provider: str
    api_key: str
    base_url: str
    model_name: str

    def key(self) -> str:
        """Stable cache key for endpoint-specific client instances."""

        return f"{self.provider}|{self.base_url}|{self.model_name}"


def _provider_runtime_config(provider: str) -> tuple[Optional[str], str, str, List[str]]:
    """Return provider runtime settings."""

    if provider == "openai":
        return (
            settings.OPENAI_API_KEY,
            settings.OPENAI_BASE_URL,
            settings.OPENAI_MODEL_NAME,
            _split_csv_models(getattr(settings, "OPENAI_MODEL_CANDIDATES", "")),
        )
    if provider == "dashscope":
        return (
            settings.DASHSCOPE_API_KEY,
            settings.DASHSCOPE_BASE_URL,
            settings.DASHSCOPE_MODEL_NAME,
            _split_csv_models(getattr(settings, "DASHSCOPE_MODEL_CANDIDATES", "")),
        )
    raise RuntimeError(f"Unsupported provider: {provider}")


def resolve_llm_endpoints(
    *,
    provider_override: Optional[str] = None,
    model_name_override: Optional[str] = None,
    allow_request_override: bool = True,
) -> List[LLMEndpoint]:
    """Resolve endpoint candidates ordered by preference and failover policy."""

    default_prefer = _normalize_provider(settings.PREFERRED_LLM_PROVIDER, default="dashscope")
    provider_hint = provider_override if allow_request_override else None
    prefer = _normalize_provider(provider_hint, default=default_prefer)
    if prefer not in {"dashscope", "openai"}:
        raise RuntimeError(
            f"Unsupported PREFERRED_LLM_PROVIDER='{prefer}'. Expected 'dashscope' or 'openai'."
        )

    providers: List[str] = [prefer]
    if getattr(settings, "LLM_ENABLE_FAILOVER", True):
        fallback = _normalize_provider(getattr(settings, "LLM_FALLBACK_PROVIDER", ""), default="")
        if fallback in {"dashscope", "openai"} and fallback not in providers:
            providers.append(fallback)

    endpoints: List[LLMEndpoint] = []
    for idx, provider in enumerate(providers):
        api_key_raw, base_url, default_model, candidate_models = _provider_runtime_config(provider)
        if _is_placeholder(api_key_raw):
            continue
        api_key = str(api_key_raw).strip()
        model_candidates: List[str] = []
        if idx == 0 and allow_request_override and model_name_override:
            model_candidates.append(str(model_name_override).strip())
        model_candidates.extend(candidate_models)
        model_candidates.append(default_model)
        for model_name in _dedupe_keep_order(model_candidates):
            endpoints.append(
                LLMEndpoint(
                    provider=provider,
                    api_key=api_key,
                    base_url=base_url,
                    model_name=model_name,
                )
            )

    if endpoints:
        return endpoints

    if prefer == "openai":
        raise RuntimeError("No usable LLM endpoint: OPENAI_API_KEY/DASHSCOPE_API_KEY missing.")
    raise RuntimeError("No usable LLM endpoint: DASHSCOPE_API_KEY/OPENAI_API_KEY missing.")


def resolve_llm_config(
    *,
    provider_override: Optional[str] = None,
    model_name_override: Optional[str] = None,
    allow_request_override: bool = True,
) -> tuple[str, str, str]:
    """Resolve one active endpoint for backward-compatible callers."""

    endpoint = resolve_llm_endpoints(
        provider_override=provider_override,
        model_name_override=model_name_override,
        allow_request_override=allow_request_override,
    )[0]
    return endpoint.api_key, endpoint.base_url, endpoint.model_name


class LLMClient:
    """Call an external LLM for report and decision generation."""

    _MODEL_CONTEXT_WINDOW_HINTS: Dict[str, int] = {
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "gpt-4.1": 1_048_576,
        "gpt-5": 400_000,
        "gpt-5-mini": 400_000,
        "gpt-5.2": 400_000,
        "qwen3-max": 200_000,
        "qwen-max": 200_000,
        "qwen-vl-max": 32_000,
        "qwen-vl-plus": 32_000,
    }

    def __init__(
        self,
        api_key: Optional[str],
        base_url: str,
        model_name: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout: int,
        usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        usage_label: Optional[str] = None,
        task_id: str = "deepresearch.report",
        endpoint_chain: Optional[List[LLMEndpoint]] = None,
        provider: str = "custom",
    ) -> None:
        """Initialize the LLM client.

        Args:
            api_key (Optional[str]): API key for the LLM provider.
            base_url (str): API base URL.
            model_name (str): Model identifier.
            temperature (float): Sampling temperature.
            max_tokens (int): Max output tokens.
            timeout (int): Request timeout in seconds.
            endpoint_chain (Optional[List[LLMEndpoint]]): Ordered failover endpoints.
            provider (str): Provider label when endpoint_chain is not provided.
        """

        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._logger = logging.getLogger("deep_research.llm_client")
        self._usage_callback = usage_callback
        self._usage_label = usage_label or "llm"
        self._task_id = str(task_id or "deepresearch.report").strip()
        self._last_usage: Optional[Dict[str, Any]] = None
        self._client_cache: Dict[str, AsyncOpenAI] = {}
        self._policy_resolver = get_policy_resolver()

        primary_endpoint = LLMEndpoint(
            provider=provider or "custom",
            api_key=str(api_key or "").strip(),
            base_url=base_url,
            model_name=model_name,
        )
        self._endpoint_chain = endpoint_chain or [primary_endpoint]
        self._endpoint_chain = [ep for ep in self._endpoint_chain if ep.api_key]
        self._active_endpoint = self._endpoint_chain[0] if self._endpoint_chain else None

    def get_last_usage(self) -> Optional[Dict[str, Any]]:
        """Return the latest usage payload from the LLM call."""

        return self._last_usage

    def get_policy_snapshot(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Return resolved policy values for observability/audit."""

        resolved_model = str(model_name or "").strip()
        if not resolved_model and self._active_endpoint is not None:
            resolved_model = self._active_endpoint.model_name
        if not resolved_model and self._endpoint_chain:
            resolved_model = self._endpoint_chain[0].model_name
        policy = self._resolve_task_policy(
            model_name=resolved_model,
            override_max_tokens=self._max_tokens,
            override_temperature=self._temperature,
            override_timeout_secs=self._timeout,
        )
        return {
            "policy_version": policy.policy_version,
            "policy_source": getattr(
                policy,
                "policy_source",
                getattr(self._policy_resolver, "policy_source", "manifest"),
            ),
            "task_id": policy.task_id,
            "model_name": resolved_model,
            "token_param": policy.token_param,
            "max_output_tokens": policy.max_output_tokens,
            "temperature": policy.temperature,
            "timeout_secs": policy.timeout_secs,
        }

    @staticmethod
    def estimate_context_window_tokens(model_name: Optional[str]) -> int:
        """Return an estimated context-window size for a model."""

        name = str(model_name or "").strip().lower()
        if not name:
            return 128_000
        if name in LLMClient._MODEL_CONTEXT_WINDOW_HINTS:
            return LLMClient._MODEL_CONTEXT_WINDOW_HINTS[name]
        if name.startswith("gpt-5"):
            return 400_000
        if name.startswith("gpt-4.1"):
            return 1_048_576
        if name.startswith("gpt-4o"):
            return 128_000
        if name.startswith("qwen-vl"):
            return 32_000
        if name.startswith("qwen"):
            return 200_000
        return 128_000

    def _resolve_task_policy(
        self,
        *,
        model_name: str,
        override_max_tokens: Optional[int] = None,
        override_temperature: Optional[float] = None,
        override_timeout_secs: Optional[float] = None,
    ):
        return self._policy_resolver.resolve(
            task_id=self._task_id,
            model_name=model_name,
            override_max_output_tokens=override_max_tokens,
            override_temperature=override_temperature,
            override_timeout_secs=override_timeout_secs,
        )

    def _uses_max_completion_tokens(self, model_name: Optional[str]) -> bool:
        resolved = self._resolve_task_policy(model_name=str(model_name or ""))
        return resolved.token_param == "max_completion_tokens"

    def get_active_endpoint(self) -> Optional[LLMEndpoint]:
        """Return the endpoint that last generated successfully."""

        return self._active_endpoint

    def is_configured(self) -> bool:
        """Check whether the client has at least one usable endpoint."""

        return bool(self._endpoint_chain)

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Generate a completion for the given prompt."""

        if not self.is_configured():
            raise RuntimeError("LLM client not configured; missing API key.")

        policy_model = ""
        if self._active_endpoint is not None:
            policy_model = self._active_endpoint.model_name
        elif self._endpoint_chain:
            policy_model = self._endpoint_chain[0].model_name
        resolved_policy = self._resolve_task_policy(
            model_name=policy_model,
            override_max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
            override_temperature=temperature if temperature is not None else self._temperature,
            override_timeout_secs=self._timeout,
        )
        max_output_tokens = resolved_policy.max_output_tokens
        sampled_temperature = resolved_policy.temperature

        last_exc: Optional[Exception] = None
        for idx, endpoint in enumerate(self._endpoint_chain, start=1):
            try:
                content = await self._generate_once(
                    endpoint=endpoint,
                    prompt=prompt,
                    max_output_tokens=max_output_tokens,
                    sampled_temperature=sampled_temperature,
                )
                self._active_endpoint = endpoint
                if idx > 1:
                    self._logger.warning(
                        "LLM failover succeeded on endpoint=%s/%s",
                        endpoint.provider,
                        endpoint.model_name,
                    )
                return content
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self._logger.warning(
                    "LLM endpoint failed (%s/%s): %s",
                    endpoint.provider,
                    endpoint.model_name,
                    exc,
                )
                continue

        if last_exc is not None:
            raise RuntimeError(f"LLM generation failed after endpoint failover: {last_exc}") from last_exc
        raise RuntimeError("LLM generation failed unexpectedly with no endpoint attempts.")

    async def _generate_once(
        self,
        *,
        endpoint: LLMEndpoint,
        prompt: str,
        max_output_tokens: int,
        sampled_temperature: float,
    ) -> str:
        """Generate a completion from one endpoint."""

        client_key = endpoint.key()
        client = self._client_cache.get(client_key)
        if client is None:
            client = AsyncOpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)
            self._client_cache[client_key] = client

        resolved_policy = self._resolve_task_policy(
            model_name=endpoint.model_name,
            override_max_tokens=max_output_tokens,
            override_temperature=sampled_temperature,
            override_timeout_secs=self._timeout,
        )
        request_kwargs: Dict[str, Any] = {
            "model": endpoint.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": resolved_policy.timeout_secs,
        }
        if resolved_policy.send_temperature:
            request_kwargs["temperature"] = resolved_policy.temperature
        if resolved_policy.token_param == "max_completion_tokens":
            request_kwargs["max_completion_tokens"] = resolved_policy.max_output_tokens
        else:
            request_kwargs["max_tokens"] = resolved_policy.max_output_tokens

        response = await client.chat.completions.create(**request_kwargs)
        self._record_usage(response, endpoint=endpoint)
        content = response.choices[0].message.content if response.choices else None
        if not content or not str(content).strip():
            raise RuntimeError("LLM returned empty content.")
        return str(content)

    def _record_usage(self, response: Any, *, endpoint: LLMEndpoint) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        prompt_tokens = self._read_usage_value(usage, "prompt_tokens")
        completion_tokens = self._read_usage_value(usage, "completion_tokens")
        total_tokens = self._read_usage_value(usage, "total_tokens")
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        total_tokens = int(total_tokens or 0)
        payload = {
            "label": self._usage_label,
            "provider": endpoint.provider,
            "model": getattr(response, "model", None) or endpoint.model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "policy_version": self._policy_resolver.policy_version,
            "policy_source": getattr(self._policy_resolver, "policy_source", "manifest"),
            "task_id": self._task_id,
        }
        self._last_usage = payload
        if self._usage_callback:
            self._usage_callback(payload)

    @staticmethod
    def _read_usage_value(usage: Any, key: str) -> Optional[int]:
        if hasattr(usage, key):
            return getattr(usage, key)
        if isinstance(usage, dict):
            return usage.get(key)
        return None
